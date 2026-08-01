"""El histórico de versiones publicadas y el diff de permisos (ADR 0142, D7).

> **Nombre.** El plan decía `marketplace/versions.py`. Este paquete ya tiene un
> `versioning.py` —la aritmética semver del plan 09 (`is_outdated`,
> `select_update_target`)—, y dos módulos llamados `versions` y `versioning` uno
> al lado del otro son un import equivocado esperando a ocurrir. Éste va de las
> filas de `marketplace_listing_versions`, así que se llama como ellas. La
> desviación está anotada en la casilla `task_mkt2_11` del plan.

Dos cosas viven aquí:

* :func:`permission_diff` — **puro**. Compara lo que una versión pedía con lo
  que pide la nueva y devuelve `added` / `removed` / `changed`. Es lo que hace
  posible que la actualización re-pregunte SOLO por el delta (D7): re-preguntar
  por todo enseña al operador a aceptar sin leer, y no re-preguntar por nada
  deja entrar un permiso nuevo sin que nadie lo mire.
* :func:`snapshot_version` — el alta de la fila de versión al publicar, que es
  lo que convierte «el manifest de hoy» en un registro histórico que un rollback
  puede recuperar.

## Por qué `changed` existe y no es una sutileza

Un permiso se identifica por su `type` (la misma regla que
`consent._index_by_type`, y tenerlas distintas haría que el diff y el
consentimiento discrepasen sobre qué se concede). Pero `allowed_domains:
["api.acme.com"]` → `allowed_domains: ["*"]` es el MISMO tipo con un alcance
radicalmente mayor. Sin la categoría `changed`, esa ampliación viajaría como
«sin cambios» y entraría sin consentimiento. Cuenta como delta y exige que
alguien lo mire.

Y el reverso: reordenar una lista NO es un cambio. Sin normalizar el orden,
cualquier re-serialización del manifest levantaría un falso aviso, y los avisos
falsos se acaban ignorando — que es la forma barata de perder el mecanismo
entero.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.marketplace import MarketplaceListing, MarketplaceListingVersion
from api_server.marketplace.consent import permission_type


def _permission_list(source: Any) -> list[Any]:
    """Los descriptores, venga un manifest o una lista pelada.

    Las dos formas circulan: la fila de versión guarda `requested_permissions`
    en su propia columna, y un manifest recién parseado los lleva dentro. Que la
    función admita ambas evita que cada llamante recuerde de dónde sacarlos.
    """
    if source is None:
        return []
    if isinstance(source, list):
        return list(source)
    if isinstance(source, dict):
        raw = source.get("requested_permissions")
        return list(raw) if isinstance(raw, list) else []
    return []


def _normalize_value(value: Any) -> Any:
    """El valor en forma comparable: las listas ordenadas, lo demás tal cual.

    Solo el primer nivel. Un `value` que sea una lista de dicts (que hoy no
    existe en `PERMISSION_KEYS`) caería al `str()` de respaldo, que sigue siendo
    estable y conservador: ante la duda, «cambió».
    """
    if isinstance(value, list):
        try:
            return sorted(value)
        except TypeError:  # elementos no comparables entre sí
            return sorted(map(str, value))
    return value


def _index(descriptors: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """`{type: descriptor}`. Un tipo repetido colapsa al último (regla de consent)."""
    out: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        # `permission_type` levanta `ConsentError` (un `ValueError`) ante un
        # descriptor malformado. Se deja subir a propósito: tragárselo dejaría
        # un permiso fuera del diff y, por tanto, fuera del consentimiento.
        out[permission_type(descriptor)] = dict(descriptor)
    return out


@dataclass(frozen=True, slots=True)
class PermissionDelta:
    """Qué cambió entre dos versiones, en las tres formas que importan."""

    #: Tipos que la versión nueva pide y la vieja no. Exigen consentimiento.
    added: tuple[dict[str, Any], ...] = ()
    #: Tipos que la versión nueva ya NO pide. No exigen nada; se enseñan.
    removed: tuple[dict[str, Any], ...] = ()
    #: Mismo tipo, distinto `value`. `{"type", "from", "to"}`. Exigen mirada.
    changed: tuple[dict[str, Any], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    @property
    def requires_consent(self) -> bool:
        """¿Hay que volver a preguntar? Solo si algo se añadió o se amplió."""
        return bool(self.added or self.changed)

    @property
    def added_types(self) -> tuple[str, ...]:
        return tuple(str(p["type"]) for p in self.added)

    @property
    def changed_types(self) -> tuple[str, ...]:
        return tuple(str(p["type"]) for p in self.changed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": [dict(p) for p in self.added],
            "removed": [dict(p) for p in self.removed],
            "changed": [dict(p) for p in self.changed],
            "requires_consent": self.requires_consent,
        }


def permission_diff(old: Any, new: Any) -> PermissionDelta:
    """El delta de permisos entre dos manifests (o dos listas de descriptores).

    Args:
        old: manifest o lista de la versión que la instalación tiene pinada.
        new: manifest o lista de la versión candidata.

    Raises:
        ValueError: un descriptor malformado o de tipo desconocido, por
            :func:`api_server.marketplace.consent.permission_type`.
    """
    old_map = _index(_permission_list(old))
    new_map = _index(_permission_list(new))

    added = tuple(new_map[t] for t in sorted(set(new_map) - set(old_map)))
    removed = tuple(old_map[t] for t in sorted(set(old_map) - set(new_map)))

    changed: list[dict[str, Any]] = []
    for ptype in sorted(set(old_map) & set(new_map)):
        before = old_map[ptype].get("value")
        after = new_map[ptype].get("value")
        if _normalize_value(before) != _normalize_value(after):
            changed.append({"type": ptype, "from": before, "to": after})

    return PermissionDelta(added=added, removed=removed, changed=tuple(changed))


# ---------------------------------------------------------------------------
# El alta de la fila de versión (el otro extremo del mismo mecanismo)
# ---------------------------------------------------------------------------
async def snapshot_version(
    session: AsyncSession,
    *,
    listing: MarketplaceListing,
    changelog: str | None = None,
    published_by: UUID | None = None,
) -> MarketplaceListingVersion:
    """Congela el estado ACTUAL del listing como fila de versión.

    Get-or-create sobre el UNIQUE `(listing_id, version)`: re-publicar la misma
    semver no crea una segunda entrada de histórico, la ACTUALIZA con el
    manifest nuevo. Eso es deliberado y es la única concesión al carácter
    append-only de la tabla: mientras la versión está en `pending_review` el
    autor puede corregirla, y obligarle a inventarse un `1.0.1` por cada
    corrección durante la revisión llenaría el histórico de versiones que nunca
    existieron. En cuanto se aprueba, la fila deja de moverse (el flujo de
    publicación exige bump de versión para volver a la cola).

    El `config_schema` se rompe fuera del manifest a propósito: el formulario del
    despliegue lo busca ahí sin tener que recorrer el manifest entero.
    """
    existing = (
        await session.execute(
            select(MarketplaceListingVersion).where(
                MarketplaceListingVersion.listing_id == listing.id,
                MarketplaceListingVersion.version == listing.version,
            )
        )
    ).scalar_one_or_none()

    manifest: dict[str, Any] = dict(listing.manifest or {})
    raw_schema = manifest.get("config_schema")
    schema = dict(raw_schema) if isinstance(raw_schema, dict) else None
    permissions = list(listing.requested_permissions or [])

    if existing is not None:
        existing.manifest = manifest
        existing.requested_permissions = permissions
        existing.config_schema = schema
        if changelog is not None:
            existing.changelog = changelog
        await session.flush()
        return existing

    row = MarketplaceListingVersion(
        listing_id=listing.id,
        tenant_id=listing.tenant_id,
        version=listing.version,
        manifest=manifest,
        requested_permissions=permissions,
        config_schema=schema,
        changelog=changelog,
        published_by=published_by,
    )
    session.add(row)
    await session.flush()
    return row


async def pinned_version(
    session: AsyncSession, *, pinned_version_id: UUID | None
) -> MarketplaceListingVersion | None:
    """La fila pinada, o ``None`` si la instalación no tiene pin.

    Sin pin (una instalación anterior a la 0128 cuyo backfill no encontró
    versión, o un listing global sin fila) el llamante cae al manifest vivo del
    listing. Es una degradación honesta: el diff sale vacío y el update no
    inventa un delta que no puede calcular.
    """
    if pinned_version_id is None:
        return None
    return (
        await session.execute(
            select(MarketplaceListingVersion).where(
                MarketplaceListingVersion.id == pinned_version_id
            )
        )
    ).scalar_one_or_none()


__all__ = [
    "PermissionDelta",
    "permission_diff",
    "pinned_version",
    "snapshot_version",
]
