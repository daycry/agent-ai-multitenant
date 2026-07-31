"""ADR 0135 — la huella canónica de una acción autorizada por un humano.

Las DOS reglas de normalización son parte de la decisión del operador, no del
cómo, así que se fijan aquí una por una:

1. **Se hashea TODO el ``args``, verbatim.** La única normalización permitida es
   estructural y sin pérdida: canonicalizar el nombre de la tool
   (:func:`shared_domain.tool_names.to_canonical`, ADR 0048), serializar con
   claves ordenadas y UTF-8, y SHA-256. NO se recorta espacio en blanco, NO se
   baja a minúsculas, NO se omiten campos «poco importantes» — un hash laxo
   autoriza más de lo que el revisor leyó.
2. Lo que se hashea es ``(tool, args)`` del ``ApprovalRequest.action``
   persistido, que es lo que la UI enseñó.

El ADR además señala por qué NO vale reutilizar ``LoopDetector._fingerprint``:
``json.dumps(..., default=str)`` hace colisionar dos objetos distintos con el
mismo ``str()``. Aquí eso es una autorización de más, así que la serialización
falla cerrado (``None``) en vez de inventarse una representación.
"""

from __future__ import annotations

import pytest
from shared_domain.approval_action import (
    action_fingerprint,
    canonical_tool_key,
    changed_args,
)


def test_identical_action_has_the_same_fingerprint() -> None:
    args = {"path": "src/app.py", "content": "print('hola')\n"}
    assert action_fingerprint("write_file", args) == action_fingerprint("write_file", dict(args))


def test_key_order_does_not_change_the_fingerprint() -> None:
    """Estructural y sin pérdida: `sort_keys` es la única reordenación."""
    a = {"path": "src/app.py", "content": "x"}
    b = {"content": "x", "path": "src/app.py"}
    assert action_fingerprint("write_file", a) == action_fingerprint("write_file", b)


@pytest.mark.parametrize(
    "mutated",
    [
        {"path": "src/app.py", "content": "print('hola') "},  # un espacio al final
        {"path": "src/app.py", "content": "PRINT('hola')"},  # mayúsculas
        {"path": "src/app.py ", "content": "print('hola')"},  # espacio en el path
        {"path": "src/app.py", "content": "print('hola')", "mode": "w"},  # campo extra
        {"path": "src/app.py"},  # campo omitido
    ],
)
def test_no_lossy_normalisation(mutated: dict[str, object]) -> None:
    """Cada una de estas mutaciones DEBE cambiar la huella.

    Si alguna no la cambia, la autorización cubre algo que el revisor no leyó:
    ese es exactamente el fallo que el ADR 0135 nombra («un hash demasiado laxo
    autoriza más de lo que el humano leyó»).
    """
    base = {"path": "src/app.py", "content": "print('hola')"}
    assert action_fingerprint("write_file", base) != action_fingerprint("write_file", mutated)


def test_alias_and_canonical_name_agree() -> None:
    """ADR 0048: `file_write` y `write_file` son la MISMA tool.

    Sin esto un alias evade la autorización (vuelve a aparcar) o, peor, la
    autorización de un alias no compara nunca y el mecanismo muere en silencio.
    """
    args = {"path": "a.py", "content": "x"}
    assert action_fingerprint("file_write", args) == action_fingerprint("write_file", args)
    assert canonical_tool_key("file_write") == "write_file"


def test_different_tools_never_share_a_fingerprint() -> None:
    """Lo que separa G1 de G4: aprobar `write_file` no autoriza `shell_exec`,
    aunque compartan la categoría `code_changes`."""
    args = {"path": "a.py"}
    assert action_fingerprint("write_file", args) != action_fingerprint("shell_exec", args)


def test_ambiguous_alias_does_not_authorise_a_concrete_verb() -> None:
    """`http_request` expande a los DOS verbos (ADR 0048), así que no puede
    identificar una acción concreta: su clave es la del par, y no coincide con
    la de `http_get` ni con la de `http_post`. Falla cerrado."""
    assert canonical_tool_key("http_request") == "http_get|http_post"
    args = {"url": "https://example.test"}
    assert action_fingerprint("http_request", args) != action_fingerprint("http_get", args)
    assert action_fingerprint("http_request", args) != action_fingerprint("http_post", args)


def test_unserialisable_args_fail_closed() -> None:
    """Sin representación canónica no hay autorización posible: `None` (que el
    gate traduce en «aparcar»), nunca un `default=str` que colisione."""
    assert action_fingerprint("write_file", {"blob": object()}) is None
    assert action_fingerprint("write_file", {"n": float("nan")}) is None
    assert action_fingerprint("", {"path": "a"}) is None
    assert action_fingerprint(None, {"path": "a"}) is None


def test_none_args_and_empty_args_are_different() -> None:
    """«sin argumentos» y «no sé los argumentos» no son lo mismo: un gate que
    no recibe args no puede canjear una autorización de `{}`."""
    assert action_fingerprint("write_file", None) != action_fingerprint("write_file", {})


def test_fingerprint_is_a_sha256_hexdigest() -> None:
    fingerprint = action_fingerprint("write_file", {"path": "a.py"})
    assert fingerprint is not None
    assert len(fingerprint) == 64
    assert set(fingerprint) <= set("0123456789abcdef")


def test_non_ascii_args_are_hashed_as_utf8() -> None:
    """UTF-8 y sin `ensure_ascii`: dos textos distintos con la misma forma
    escapada no pueden colapsar."""
    assert action_fingerprint("write_file", {"c": "camión"}) != action_fingerprint(
        "write_file", {"c": "camion"}
    )


# --- el delta que el humano ve cuando vuelve a preguntar (N3) -----------------
def test_changed_args_reports_added_removed_and_modified() -> None:
    delta = changed_args({"path": "a.py", "mode": "w"}, {"path": "b.py", "encoding": "utf-8"})
    assert delta["path"] == {"before": "a.py", "after": "b.py"}
    assert delta["mode"] == {"before": "w", "after": None}
    assert delta["encoding"] == {"before": None, "after": "utf-8"}


def test_changed_args_ignores_untouched_keys() -> None:
    delta = changed_args({"path": "a.py", "content": "x"}, {"path": "a.py", "content": "y"})
    assert "path" not in delta
    assert delta["content"] == {"before": "x", "after": "y"}


def test_changed_args_truncates_huge_values_but_still_reports_them() -> None:
    """El delta es una AYUDA de lectura, no la autorización: se acota para que
    no infle el JSONB ni el prompt. La huella no lo mira."""
    delta = changed_args({"content": "a" * 5000}, {"content": "b" * 5000})
    rendered = delta["content"]
    assert isinstance(rendered["before"], str)
    assert len(rendered["before"]) < 5000
    assert rendered["before"].endswith("…")


def test_changed_args_handles_non_dict_args() -> None:
    delta = changed_args(["a"], ["b"])
    assert delta == {"": {"before": "['a']", "after": "['b']"}}
