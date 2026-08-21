"""Cachés Redis de lectura del api-server (prod-13 · perf-10).

Un paquete propio, y no una función más en cada módulo de `db/`, porque estas
cachés comparten una disciplina que conviene tener escrita en un solo sitio:

  * **TTL corto y explícito**: es el techo del daño si una invalidación se
    pierde, no un parámetro de rendimiento;
  * **invalidación al escribir**, no "ya expirará";
  * **best-effort sobre el caché, nunca sobre la decisión**: Redis caído
    degrada a la consulta de siempre contra PostgreSQL —que sigue siendo la
    verdad—, jamás a un 500 ni a un "pues que pase".

`db/platform_settings.py` sigue teniendo la suya en casa por precedencia; lo que
vive aquí es lo que toca AUTORIZACIÓN, donde una entrada rancia no es un valor
viejo sino un permiso que ya no existe.
"""

from __future__ import annotations

__all__: list[str] = []
