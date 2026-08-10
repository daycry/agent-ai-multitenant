"""Hidratación de la credencial del modelo desde el mount (prod-07 task_prod07_10).

El worker dejó de meter la credencial del proveedor LLM en ``AGENT_TASK_SPEC``:
ahora la escribe en un fichero read-only bajo ``/run/secrets`` y en el spec sólo
viaja el puntero (``model.credentials_file``). Este módulo es la contraparte:
lee ese fichero UNA vez, al arrancar, y superpone su contenido sobre
``spec["model"]`` para que todo lo de aguas abajo —``model_from_spec``,
``build_provider_client``, los cuatro adaptadores— siga viendo exactamente el
mismo spec que veía antes. Ni una firma cambia.

**Acepta los dos formatos a propósito.** Sin la clave del puntero no hace nada,
así que una imagen con este módulo sigue funcionando con un worker antiguo que
mande la credencial en línea. Esa asimetría es la que permite desplegar la imagen
ANTES que el worker, que es el único orden que no rompe runs en vuelo.

Qué pasa si el fichero no está: se emite el aviso y se sigue. Reventar aquí
convertiría un fallo de montaje en un arranque sin diagnóstico; dejándolo pasar,
el provider falla con su 401 y el `execution.error` dice de dónde venía. El aviso
es la pista que ata las dos cosas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CREDENTIALS_FILE_KEY = "credentials_file"


def hydrate_model_credentials(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copia de ``spec`` con la credencial del fichero superpuesta en ``model``.

    Devuelve el spec intacto cuando no hay puntero (formato antiguo, o un modelo
    sin credencial). El puntero se ELIMINA tras hidratar: dejarlo puesto haría
    que un volcado del spec —los hay en los eventos de depuración— publicara la
    ruta del mount, que no es un secreto pero sí un mapa.
    """
    if not spec:
        return spec
    model = spec.get("model")
    if not isinstance(model, dict):
        return spec
    pointer = model.get(CREDENTIALS_FILE_KEY)
    if not isinstance(pointer, str) or not pointer:
        return spec

    hydrated_model = {k: v for k, v in model.items() if k != CREDENTIALS_FILE_KEY}
    hydrated_model.update(_read_credentials(pointer))
    return {**spec, "model": hydrated_model}


def _read_credentials(path: str) -> dict[str, str]:
    """El JSON del mount, o ``{}`` con un aviso si no hay forma de leerlo."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        _warn(f"no se pudo leer el fichero de credenciales del modelo ({path}): {exc}")
        return {}
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        _warn(f"el fichero de credenciales del modelo ({path}) no es JSON válido: {exc}")
        return {}
    if not isinstance(payload, dict):
        _warn(f"el fichero de credenciales del modelo ({path}) no es un objeto JSON")
        return {}
    # Sólo cadenas: un valor no-str en un campo de credencial es un fichero
    # corrupto, y pasarlo al adaptador produciría un TypeError muy lejos de aquí.
    return {str(k): v for k, v in payload.items() if isinstance(v, str) and v}


def _warn(message: str) -> None:
    """Un aviso por el canal estructurado del runtime (una línea JSON en stdout).

    Sin `logging`: el worker parsea stdout línea a línea y una línea que no sea
    JSON le ensucia el stream de eventos.
    """
    print(  # el canal del runtime ES stdout
        json.dumps({"event": "runtime.warning", "warning": "model_credentials", "detail": message}),
        flush=True,
    )


__all__ = [
    "CREDENTIALS_FILE_KEY",
    "hydrate_model_credentials",
]
