"""Huella canónica de una acción sensible aprobada por un humano (ADR 0135).

El ADR 0135 decidió que **aprobar autoriza esa acción exacta, en esa task, una
vez** (G1+S1+T1+N3). Para comparar «la acción que el humano leyó» con «la acción
que el modelo acaba de proponer» hacen falta dos cosas que este módulo da, y una
sola vez:

* :func:`canonical_tool_key` — el nombre de la tool resuelto por el alias layer
  del ADR 0048, para que ``file_write`` y ``write_file`` no sean dos acciones
  distintas ni fallen al compararse.
* :func:`action_fingerprint` — SHA-256 sobre ``{tool_canónico, args}``
  serializado con claves ordenadas y UTF-8.

**Las dos reglas de normalización son parte de la DECISIÓN, no del cómo**: se
hashea TODO el ``args``, verbatim. No se recorta espacio en blanco, no se baja a
minúsculas, no se omiten campos «poco importantes». Un hash laxo autoriza más de
lo que el revisor leyó, y el sitio donde eso se materializa es exactamente aquí.

Vive en ``shared-domain`` porque lo necesitan los DOS extremos y no pueden
importarse entre sí: el api-server/worker (que lee el ``ApprovalRequest.action``
persistido y emite la lista autorizada) y el agent-runtime **sandboxado** (que
compara antes de aparcar). Una copia mirror en cada lado es cómo se divergen las
cosas en este repo — y aquí divergir significa que la autorización deja de
coincidir en silencio y el bucle vuelve sin que nadie lo note.

Por qué NO se reutiliza ``LoopDetector._fingerprint`` (que es
``json.dumps(action, sort_keys=True, default=str)``): está calibrado para
detectar repetición DENTRO de un run, no para decidir una autorización, y su
``default=str`` hace colisionar dos objetos distintos con el mismo ``str()``.
Aquí una colisión es una autorización de más, así que la serialización falla
CERRADO (devuelve ``None``, que el gate traduce en «aparcar»).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from shared_domain.tool_names import to_canonical

__all__ = [
    "ACTION_HASH_ALGORITHM",
    "CHANGED_VALUE_MAX_CHARS",
    "action_fingerprint",
    "canonical_tool_key",
    "changed_args",
]

#: El algoritmo, nombrado para que la lista serializada pueda decir cuál usó.
ACTION_HASH_ALGORITHM = "sha256"

#: Tope de caracteres por valor en el delta que se le enseña al humano (N3). El
#: delta es una AYUDA de lectura: no entra en la huella, así que acotarlo no
#: relaja ninguna autorización — solo evita inflar el JSONB y el prompt con el
#: cuerpo entero de un fichero.
CHANGED_VALUE_MAX_CHARS = 400


def canonical_tool_key(tool: str | None) -> str:
    """La clave estable de una tool, resuelto su alias (ADR 0048).

    ``to_canonical`` devuelve un ``frozenset`` porque UN alias
    (``http_request``) expande a los dos verbos HTTP. Para identificar una
    acción hace falta una clave determinista, así que se unen ordenados:

    * ``file_write`` y ``write_file`` → ``"write_file"`` (comparan igual);
    * ``http_request`` → ``"http_get|http_post"``, que NO coincide con
      ``"http_get"`` ni con ``"http_post"``. Es deliberado: un nombre ambiguo no
      puede autorizar un verbo concreto, y fallar cerrado (volver a preguntar)
      es la dirección segura.

    Cadena vacía cuando no hay nombre — el llamante lo trata como «sin huella».
    """
    if not tool:
        return ""
    return "|".join(sorted(to_canonical(str(tool))))


def action_fingerprint(tool: str | None, args: Any) -> str | None:
    """SHA-256 hexadecimal de ``(tool canónico, args verbatim)``.

    Devuelve ``None`` —y el llamante DEBE tratarlo como «no autorizado»— cuando
    la acción no admite representación canónica: sin nombre de tool, o con
    ``args`` no serializables a JSON (objetos arbitrarios, ``NaN``/``Infinity``,
    claves no-string). Nada de ``default=str``: una representación inventada
    colisiona, y una colisión aquí es una autorización que nadie concedió.
    """
    key = canonical_tool_key(tool)
    if not key:
        return None
    try:
        payload = json.dumps(
            {"tool": key, "args": args},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _render(value: Any) -> Any:
    """Un valor listo para enseñar: acotado, y sin perder el tipo si es corto."""
    if value is None or isinstance(value, bool | int | float):
        return value
    text = value if isinstance(value, str) else repr(value)
    if len(text) > CHANGED_VALUE_MAX_CHARS:
        return text[:CHANGED_VALUE_MAX_CHARS] + "…"
    return text


def changed_args(prior: Any, current: Any) -> dict[str, dict[str, Any]]:
    """Qué cambió entre los args aprobados antes y los propuestos ahora (N3).

    El ADR 0135 eligió N3 —«re-aparcar, pero enseñando el diff»— justo porque un
    LLM no es determinista: la segunda solicitud lleva el delta para que el
    humano confirme en dos segundos en vez de releerlo todo. Esto NO relaja la
    autorización: es texto para el revisor, y la huella sigue calculándose sobre
    el ``args`` entero.

    Formato ``{clave: {"before": …, "after": …}}``; una clave ausente en un lado
    aparece con ``None``. Args que no son dict se comparan como un todo bajo la
    clave ``""``.
    """
    if not isinstance(prior, dict) or not isinstance(current, dict):
        if prior == current:
            return {}
        return {"": {"before": _render(prior), "after": _render(current)}}

    delta: dict[str, dict[str, Any]] = {}
    for key in sorted(set(prior) | set(current)):
        before = prior.get(key)
        after = current.get(key)
        if before == after and key in prior and key in current:
            continue
        delta[str(key)] = {"before": _render(before), "after": _render(after)}
    return delta
