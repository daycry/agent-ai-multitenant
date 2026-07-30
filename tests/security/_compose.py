"""Qué cuenta como «servicio» al auditar los compose de producción.

No es un módulo de tests (el prefijo `_` lo mantiene fuera de la colecta): es la
regla que las tres suites de seguridad necesitaban y ninguna tenía.

El stack se levanta apilando ficheros (`docker compose -f … -f …`), y en un
overlay conviven dos cosas distintas:

* **Servicios de verdad**, con su `image` o su `build`. Su endurecimiento tiene
  que estar declarado ahí: no hay de dónde heredarlo.
* **Fragmentos de override**, que solo parchean un servicio definido en OTRO
  fichero. `workers:` en `docker-compose.monitoring.yml` solo añade un volumen y
  un `depends_on`; su `security_opt`, su `cap_drop` y su perfil AppArmor vienen
  del fichero que lo define de verdad. Compose ni siquiera aceptaría ese bloque
  como definición autónoma: fallaría con «no image or build context specified».

Leer un fragmento como si fuera un servicio autónomo lo denunciaba por «sin
endurecer». Ese falso positivo mantuvo la suite de seguridad ENTERA en rojo, y
con ella tapados los hallazgos de verdad — entre ellos un servicio nuevo
efectivamente sin endurecer (`textfile-init`) en ese MISMO fichero, y una tabla
con `tenant_id` sin RLS. Una suite que siempre falla no es una suite.

Ojo con el alcance de la regla: el `docker-compose.yml` versionado trae solo la
capa de INFRAESTRUCTURA (postgres, redis, vault, minio…). Los servicios de la
aplicación —api-server, workers, orchestrator— no están en ningún compose del
repo: los **genera el instalador**, y su endurecimiento lo cubren los tests del
`compose_generator`. Por eso aquí no queda nadie a quien pedirle cuentas por el
fragmento, y saltárselo no deja un hueco sin vigilar.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_override_fragment(spec: Mapping[str, Any]) -> bool:
    """``True`` si el bloque parchea un servicio definido en otro fichero.

    El discriminador es estructural, no una lista de nombres: sin ``image`` ni
    ``build`` no hay nada que arrancar, así que no es una definición. Una lista
    de exenciones por nombre se habría quedado obsoleta al primer servicio nuevo
    — y en silencio, que es lo malo.
    """
    return not spec.get("image") and not spec.get("build")


def defined_services(
    services: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Solo los servicios que este fichero DEFINE, sin los fragmentos."""
    return {name: dict(spec) for name, spec in services.items() if not is_override_fragment(spec)}


__all__ = ["defined_services", "is_override_fragment"]
