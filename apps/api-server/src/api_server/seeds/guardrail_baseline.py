"""El baseline de guardrails de plataforma, con candados (prod-03 task_prod03_08).

`shared_guardrails.layers` lleva desde el Plan 11 prometiendo esto en su primer
párrafo — «PII / secret-leakage / prompt-injection baselines live here and are
mandatory»— y nunca hubo baseline: `LockedFieldOverrideError` no tenía un solo
llamante fuera de tests, porque no había ningún guardrail `locked` que un tenant
pudiera intentar relajar. Los candados eran una promesa del docstring.

Qué se siembra, y por qué esos tres
-----------------------------------
Los tres que el plan nombra, cada uno en el hook donde el dato entra o sale:

* ``prompt_injection`` en ``post_tool`` — es el que cierra la **inyección
  indirecta**: lo que devuelve una tool (una página web, un chunk de RAG, la
  salida de un MCP) reentra al contexto del modelo, y ahí es donde el atacante
  escribe. Se escanea ANTES de que la observación vuelva al modelo.
* ``secret_leakage`` en ``post_llm`` — una credencial que el modelo escupe en su
  respuesta ya está fuera; el sitio para verla es la salida.
* ``pii`` en ``post_llm`` — mismo razonamiento.

Los tres van ``locked: true`` (un tenant no puede quitarlos ni relajarlos) y con
la acción en **``warn``, no ``block``**, que es deliberado y es la mitigación
nº1 de riesgos del propio plan: cablear tres checks nuevos en `block` sobre
salidas reales de RAG/HTTP tumbaría ejecuciones legítimas el primer día. Se
observa `guardrail_events` una semana y se sube a `block` **con datos**. El
candado protege la EXISTENCIA del check, que es lo que un tenant no debe poder
apagar; la acción es una palanca de la plataforma, que puede subirla sin tocar
código.

Ojo con la asimetría que esto crea, porque es intencionada: al ser ``locked``,
su `on_error` efectivo es ``block`` (ADR 0102 D5). O sea: mientras el check
funcione, un hallazgo solo avisa; si el check REVIENTA, se bloquea. Es la
postura correcta para una capa obligatoria — un candado que se abre solo cuando
falla no es un candado.

Idempotente: si ya hay fila de plataforma NO se pisa. El operador puede haber
subido los tres a ``block`` o haber añadido más checks, y un seed que
reescribiera al arrancar sería un rollback silencioso de su decisión.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.guardrail_config import get_layer_config, set_layer_config

#: Los tres guardrails obligatorios de plataforma. La forma es la del config
#: declarativo de `shared_guardrails.config`.
PLATFORM_BASELINE: dict[str, Any] = {
    "guardrails": {
        "post_tool": [
            {
                "type": "prompt_injection",
                "id": "platform_prompt_injection",
                "action": "warn",
                "locked": True,
            }
        ],
        "post_llm": [
            {
                "type": "secret_leakage",
                "id": "platform_secret_leakage",
                "action": "warn",
                "locked": True,
            },
            {
                "type": "pii",
                "id": "platform_pii",
                "action": "warn",
                "locked": True,
            },
        ],
    }
}

#: Las claves de los tres, para que un test pueda exigirlas por nombre sin
#: reimplementar la travesía del dict.
BASELINE_LOCKED_KEYS: tuple[str, ...] = (
    "platform_prompt_injection",
    "platform_secret_leakage",
    "platform_pii",
)


async def seed_platform_guardrail_baseline(session: AsyncSession) -> bool:
    """Siembra el baseline si no hay capa de plataforma. ``True`` si sembró.

    Requiere una sesión con permiso para escribir la fila de plataforma (la
    RLS de la 0132 no la deja escribir desde una sesión de tenant): el seed CLI
    y el arranque usan el engine admin. El caller es dueño de la transacción.
    """
    existing = await get_layer_config(session, "platform")
    if existing is not None:
        return False
    await set_layer_config(session, "platform", PLATFORM_BASELINE)
    return True


__all__ = [
    "BASELINE_LOCKED_KEYS",
    "PLATFORM_BASELINE",
    "seed_platform_guardrail_baseline",
]
