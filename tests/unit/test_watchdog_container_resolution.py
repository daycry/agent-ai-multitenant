"""El watchdog encuentra los contenedores que dice vigilar (prod-08 task_prod08_watchdog_14).

La tarea pide ampliar la lista vigilada con el **egress-proxy**, que es la ÚNICA
salida de los agent-runtimes hacia los LLM (ADR 0019). Al ir a hacerlo aparece la
trampa: `_build_monitors` resolvía el contenedor por la convención de nombres de
Compose (`{proyecto}-{servicio}-1`), y en `docker/docker-compose.yml` tanto el
egress-proxy como el registry-proxy declaran `container_name:` explícito
(`agentic-egress-proxy`). O sea que añadirlos a la lista tal cual habría producido
un watchdog que **declara vigilarlos y no vigila ninguno**: un `container_missing`
en el log de arranque, silencio después, y la falsa sensación de cobertura — el
modo de fallo «la guarda pasa en vacío» de verificar-antes-de-implementar.md §5.

Por eso la resolución pasa a hacerse por las **etiquetas de Compose**
(`com.docker.compose.project` + `com.docker.compose.service`), que Docker pone
siempre y no dependen de cómo se llame el contenedor, con la convención de nombres
conservada solo como último recurso para stacks levantados a mano.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from watchdog.__main__ import _DEFAULT_SERVICES, resolve_container


class _NotFoundError(Exception):
    """Sustituto de docker.errors.NotFound."""


@dataclass
class FakeContainer:
    name: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeContainers:
    by_name: dict[str, FakeContainer] = field(default_factory=dict)
    by_label: dict[tuple[str, str], FakeContainer] = field(default_factory=dict)
    get_calls: list[str] = field(default_factory=list)

    def get(self, name: str) -> FakeContainer:
        self.get_calls.append(name)
        try:
            return self.by_name[name]
        except KeyError as exc:
            raise _NotFoundError(name) from exc

    def list(self, *, all: bool = False, filters: dict[str, Any] | None = None) -> list[Any]:
        labels = dict(
            item.split("=", 1) for item in (filters or {}).get("label", []) if "=" in item
        )
        key = (
            labels.get("com.docker.compose.project", ""),
            labels.get("com.docker.compose.service", ""),
        )
        found = self.by_label.get(key)
        return [found] if found is not None else []


@dataclass
class FakeClient:
    containers: FakeContainers = field(default_factory=FakeContainers)


# ---------------------------------------------------------------------------
# Resolución por etiquetas
# ---------------------------------------------------------------------------
def test_container_with_a_custom_name_is_found_by_compose_labels() -> None:
    """El caso que motiva el cambio: `container_name: agentic-egress-proxy`."""
    proxy = FakeContainer(name="agentic-egress-proxy")
    client = FakeClient(
        containers=FakeContainers(by_label={("agentic-platform", "egress-proxy"): proxy})
    )

    found = resolve_container(client, "agentic-platform", "egress-proxy", not_found=_NotFoundError)

    assert found is proxy


def test_the_naming_convention_still_works_as_a_fallback() -> None:
    """Un stack levantado sin las etiquetas de Compose sigue vigilándose."""
    postgres = FakeContainer(name="agentic-platform-postgres-1")
    client = FakeClient(
        containers=FakeContainers(by_name={"agentic-platform-postgres-1": postgres})
    )

    found = resolve_container(client, "agentic-platform", "postgres", not_found=_NotFoundError)

    assert found is postgres
    assert client.containers.get_calls == ["agentic-platform-postgres-1"]


def test_labels_win_over_the_naming_convention() -> None:
    """Si ambos resuelven, manda la etiqueta: es la que Docker garantiza."""
    labelled = FakeContainer(name="agentic-egress-proxy")
    named = FakeContainer(name="agentic-platform-egress-proxy-1")
    client = FakeClient(
        containers=FakeContainers(
            by_name={"agentic-platform-egress-proxy-1": named},
            by_label={("agentic-platform", "egress-proxy"): labelled},
        )
    )

    assert resolve_container(
        client, "agentic-platform", "egress-proxy", not_found=_NotFoundError
    ) is (labelled)
    # Ni siquiera se intentó el nombre.
    assert client.containers.get_calls == []


def test_a_missing_container_resolves_to_none() -> None:
    client = FakeClient()
    assert resolve_container(client, "agentic-platform", "ghost", not_found=_NotFoundError) is None


def test_a_broken_label_query_falls_back_instead_of_exploding() -> None:
    """Un daemon viejo que no acepte el filtro no debe dejar al watchdog ciego."""

    class ExplodingContainers(FakeContainers):
        def list(self, *, all: bool = False, filters: dict[str, Any] | None = None) -> list[Any]:
            raise RuntimeError("filter unsupported")

    postgres = FakeContainer(name="agentic-platform-postgres-1")
    client = FakeClient(
        containers=ExplodingContainers(by_name={"agentic-platform-postgres-1": postgres})
    )

    assert resolve_container(client, "agentic-platform", "postgres", not_found=_NotFoundError) is (
        postgres
    )


# ---------------------------------------------------------------------------
# La lista vigilada
# ---------------------------------------------------------------------------
def test_the_egress_proxy_is_watched() -> None:
    """ADR 0019: si muere, los agentes se quedan sin salida a los LLM y el stack
    no delata la causa. Es el servicio que la tarea pedía añadir."""
    assert "egress-proxy" in _DEFAULT_SERVICES


def test_the_registry_proxy_is_watched() -> None:
    """ADR 0094: si muere, las instalaciones de dependencias dentro de los
    runtime-templates fallan sin causa aparente. Misma imagen, mismo riesgo."""
    assert "registry-proxy" in _DEFAULT_SERVICES


def test_the_original_five_infra_services_are_still_watched() -> None:
    for service in ("postgres", "redis", "minio", "vault", "clamav"):
        assert service in _DEFAULT_SERVICES
