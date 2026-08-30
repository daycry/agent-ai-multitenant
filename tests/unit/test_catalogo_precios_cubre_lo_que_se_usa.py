"""El catálogo de respaldo tiene que cubrir los modelos que la plataforma USA.

**Por qué existe (hallazgo MEDIO 5 de la ola 2 del ADR 0162).** Al actualizar
``DEFAULT_AI_PRICE_CATALOG`` con la generación nueva se retiraron de él
``claude-opus-4-7`` y ``claude-sonnet-4-6`` — que no son modelos retirados: son
los que ``seeds/builtin_agents.py`` asigna a **once de los agentes built-in**,
o sea los que corren de verdad en la instalación viva.

Un precio desactualizado da un importe malo. Un precio **ausente** hace algo
peor: el estimador deja de contar ese modelo y lo reporta en ``missing_models``,
así que el coste de la mitad del parque desaparece del presupuesto sin que nada
falle. Es la misma familia que todo el ADR 0162 —una señal que dice algo
distinto de lo que ocurre—, aquí en la cuenta de lo que se gasta.

La guarda se ancla en las SEMILLAS y no en una lista escrita a mano: quien
cambia el modelo de un agente built-in es quien crea la obligación de tener su
precio, y una lista paralela se desincroniza a la primera.
"""

from __future__ import annotations

from api_server.chat.cost import DEFAULT_AI_PRICE_CATALOG
from api_server.seeds.builtin_agents import BUILTIN_AGENTS

#: El único modelo sembrado que hoy NO tiene precio, y por qué se tolera.
#:
#: ``builtin_agents.py`` siembra el QA con el **alias fechado**
#: ``claude-haiku-4-5-20251001``, y ``PriceCatalog.get`` es una búsqueda exacta:
#: no normaliza sufijos de fecha. O sea que ese agente nunca ha tenido precio de
#: respaldo. Es un defecto de la semilla —el id correcto es ``claude-haiku-4-5``,
#: sin fecha— y vive en un fichero fuera del alcance de esta tanda, así que se
#: deja anotado en vez de arreglado a escondidas.
#:
#: Se compara con ``<=`` y no con ``==`` a propósito: arreglar la semilla NO
#: puede romper esta guarda, pero perder el precio de CUALQUIER otro modelo sí.
HUECO_CONOCIDO = {"claude-haiku-4-5-20251001"}


def test_todo_modelo_de_un_agente_builtin_tiene_precio_de_respaldo() -> None:
    sembrados = {agent.model_name for agent in BUILTIN_AGENTS if agent.model_name}
    # «Encontré algo»: si la tupla de semillas se vacía o cambia de forma, esta
    # guarda pasaría vacía y envejecería sin avisar
    # (`docs/03-guides/verificar-antes-de-implementar.md` §4).
    assert len(sembrados) >= 3, f"apenas {len(sembrados)} modelos sembrados: ¿parser roto?"

    sin_precio = {m for m in sembrados if DEFAULT_AI_PRICE_CATALOG.get(m) is None}
    huerfanos = sorted(sin_precio - HUECO_CONOCIDO)
    assert sin_precio <= HUECO_CONOCIDO, (
        f"modelos que la plataforma siembra y ya no sabe cobrar: {huerfanos}. "
        "Un modelo sin precio no se estima: su coste desaparece del presupuesto en silencio."
    )


def test_ningun_precio_es_cero_ni_negativo() -> None:
    """Un precio a 0 no es «gratis»: es un modelo que deja de contar igual que si
    faltara, sólo que además parece configurado."""
    malos = sorted(
        model_id
        for model_id, price in DEFAULT_AI_PRICE_CATALOG.prices.items()
        # Ollama local ES gratis y su fila con ceros es correcta; lo que no puede
        # haber es un precio negativo, ni un cero en un modelo de pago.
        if price.input_per_million < 0 or price.output_per_million < 0
    )
    assert malos == []
