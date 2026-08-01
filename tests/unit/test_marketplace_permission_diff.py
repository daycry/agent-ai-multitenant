"""El diff de permisos entre dos versiones — `task_mkt2_11` (ADR 0142, D7).

Es la pieza pura sobre la que se apoya el re-consentimiento del delta: la
actualización de una instalación **solo vuelve a preguntar por lo que cambió**.
Si esta función se equivoca por exceso, el operador re-consiente cosas que ya
concedió y aprende a darle a «aceptar» sin leer; si se equivoca por defecto, un
permiso NUEVO entra sin que nadie lo mire, que es el agujero que D7 cierra.

Los descriptores son `{"type": <clave>, "value": …}` — la forma canónica que
`marketplace/consent.py` ya usa. El `type` es la identidad: dos descriptores del
mismo tipo son el mismo permiso aunque su `value` cambie… y ese cambio de
`value` es su propio caso, porque «los dominios permitidos pasan de
`api.acme.com` a `*`» NO es «nada ha cambiado».
"""

from __future__ import annotations

import pytest
from api_server.marketplace.listing_versions import PermissionDelta, permission_diff


def _perm(ptype: str, value: object = None) -> dict[str, object]:
    return {"type": ptype, "value": value}


# ---------------------------------------------------------------------------
# Lo básico
# ---------------------------------------------------------------------------
def test_no_change_is_an_empty_delta() -> None:
    perms = [_perm("allowed_domains", ["api.acme.com"])]
    delta = permission_diff({"requested_permissions": perms}, {"requested_permissions": perms})
    assert delta == PermissionDelta(added=(), removed=(), changed=())
    assert delta.is_empty is True


def test_added_permission_is_reported() -> None:
    old = {"requested_permissions": [_perm("allowed_domains", ["a.com"])]}
    new = {
        "requested_permissions": [
            _perm("allowed_domains", ["a.com"]),
            _perm("allowed_paths", ["/tmp"]),
        ]
    }
    delta = permission_diff(old, new)
    assert [p["type"] for p in delta.added] == ["allowed_paths"]
    assert delta.removed == ()
    assert delta.is_empty is False


def test_removed_permission_is_reported_and_is_not_an_addition() -> None:
    """Quitar un permiso NO exige consentimiento, pero sí se enseña.

    El operador tiene derecho a saber que la versión nueva ya no pide algo — es
    información buena, no un trámite.
    """
    old = {
        "requested_permissions": [
            _perm("allowed_domains", ["a.com"]),
            _perm("network_policy", "open"),
        ]
    }
    new = {"requested_permissions": [_perm("allowed_domains", ["a.com"])]}
    delta = permission_diff(old, new)
    assert [p["type"] for p in delta.removed] == ["network_policy"]
    assert delta.added == ()
    assert delta.requires_consent is False


# ---------------------------------------------------------------------------
# El caso que muerde: mismo tipo, alcance distinto
# ---------------------------------------------------------------------------
def test_widened_value_on_the_same_type_counts_as_changed() -> None:
    """`allowed_domains: [api.acme.com] → [*]` no es «sin cambios»."""
    old = {"requested_permissions": [_perm("allowed_domains", ["api.acme.com"])]}
    new = {"requested_permissions": [_perm("allowed_domains", ["*"])]}
    delta = permission_diff(old, new)
    assert delta.added == ()
    assert delta.removed == ()
    assert len(delta.changed) == 1
    change = delta.changed[0]
    assert change["type"] == "allowed_domains"
    assert change["from"] == ["api.acme.com"]
    assert change["to"] == ["*"]
    # Y sí exige que alguien lo mire: el alcance se amplió.
    assert delta.requires_consent is True


def test_order_of_list_values_is_not_a_change() -> None:
    """Reordenar `["a","b"]` a `["b","a"]` no amplía nada.

    Sin esta normalización, cualquier re-serialización del manifest levantaría
    un falso «cambió el permiso» y el operador acabaría ignorando los avisos.
    """
    old = {"requested_permissions": [_perm("allowed_domains", ["a.com", "b.com"])]}
    new = {"requested_permissions": [_perm("allowed_domains", ["b.com", "a.com"])]}
    assert permission_diff(old, new).is_empty is True


def test_requires_consent_is_true_when_something_is_added() -> None:
    old = {"requested_permissions": []}
    new = {"requested_permissions": [_perm("allowed_paths", ["/etc"])]}
    assert permission_diff(old, new).requires_consent is True


# ---------------------------------------------------------------------------
# Entradas que llegan de sitios distintos
# ---------------------------------------------------------------------------
def test_accepts_bare_permission_lists_as_well_as_manifests() -> None:
    """El histórico guarda `requested_permissions` aparte del manifest.

    La fila de versión tiene las dos columnas, y el listing vivo también; que la
    función admita ambas formas evita que cada llamante recuerde de dónde
    sacarlas.
    """
    old = [_perm("allowed_domains", ["a.com"])]
    new = [_perm("allowed_domains", ["a.com"]), _perm("allowed_paths", ["/tmp"])]
    delta = permission_diff(old, new)
    assert [p["type"] for p in delta.added] == ["allowed_paths"]


def test_none_and_empty_are_the_same_thing() -> None:
    assert permission_diff(None, None).is_empty is True
    assert permission_diff({}, {}).is_empty is True
    assert permission_diff(None, [_perm("allowed_paths", ["/x"])]).requires_consent is True


def test_a_malformed_descriptor_does_not_silently_vanish() -> None:
    """Un descriptor sin `type` reconocible no puede colarse como «nada».

    Tragárselo dejaría un permiso fuera del diff y, por tanto, fuera del
    consentimiento.
    """
    with pytest.raises(ValueError):
        permission_diff([], [{"tipo": "allowed_paths"}])


def test_duplicate_types_collapse_to_the_last_one() -> None:
    """Dos descriptores del mismo tipo son el mismo permiso; gana el último.

    Es la misma regla que `consent._index_by_type`, y tenerlas distintas haría
    que el diff y el consentimiento discrepasen sobre qué se está concediendo.
    """
    new = [_perm("allowed_domains", ["a.com"]), _perm("allowed_domains", ["b.com"])]
    delta = permission_diff([], new)
    assert len(delta.added) == 1
    assert delta.added[0]["value"] == ["b.com"]
