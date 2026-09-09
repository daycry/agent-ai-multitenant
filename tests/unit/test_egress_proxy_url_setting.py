"""La URL del egress-proxy es del api-server ENTERO, no del córtex (ADR 0165 D9 y A1).

El ADR 0165 manda que «Probar conexión» de un MCP remoto salga por el mismo
egress-proxy por el que sale el runtime, y ahí la URL del proxy deja de ser un
detalle de las web tools del córtex para ser infraestructura del proceso. El
campo se llama por lo que es (`egress_proxy_url`) y el nombre viejo sobrevive
como **alias de entorno**, no como segunda fuente de verdad: dos campos
independientes con el mismo valor esperado son la forma de que una instalación
antigua proxifique la web y NO la prueba de MCP, que es justo la asimetría que
el ADR viene a cerrar.

Lo que se fija aquí, y por qué cada cosa:

- **Los dos nombres de entorno llegan al mismo sitio.** Es la condición de que
  un stack ya instalado —`docker-compose.manuals.yml` lleva el nombre viejo
  desde el ADR 0067— siga funcionando sin tocar su `.env`.
- **El nuevo gana cuando están los dos.** Un `AliasChoices` resuelve por orden,
  y ese orden es una decisión: durante la transición ambos convivirán en el
  mismo compose, y quien lea el fichero espera que mande el nombre nuevo.
- **La propiedad de compatibilidad sigue al campo, no a una copia.** Tres
  llamantes vivos leen `cfg.cortex_egress_proxy_url`
  (`cortex/tools.py`, `workers/cortex_curiosity.py`); si la propiedad devolviera
  otra cosa, el córtex saldría por un proxy y el MCP por otro.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

#: El nombre que el ADR 0165 (A1) mete en el generador del instalador.
_NUEVO = "API_SERVER_EGRESS_PROXY_URL"
#: El nombre del ADR 0067, vivo en `docker/docker-compose.manuals.yml` y en
#: cualquier `.env` escrito antes de esta remediación.
_VIEJO = "API_SERVER_CORTEX_EGRESS_PROXY_URL"


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> object:
    """Construye un `Settings` con el entorno de egress LIMPIO y solo lo pedido.

    Los dos nombres se borran siempre antes de sembrar: si la máquina que corre
    los tests tuviera uno exportado, el caso «solo el nombre viejo» pasaría por
    la razón equivocada y el alias podría estar roto sin que nadie se enterase.
    """
    from api_server.config import Settings

    monkeypatch.delenv(_NUEVO, raising=False)
    monkeypatch.delenv(_VIEJO, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()


@pytest.mark.parametrize("nombre", [_NUEVO, _VIEJO])
def test_los_dos_nombres_de_entorno_dan_la_misma_url(
    monkeypatch: pytest.MonkeyPatch, nombre: str
) -> None:
    cfg = _settings(monkeypatch, **{nombre: "http://egress-proxy:8888"})

    assert cfg.egress_proxy_url == "http://egress-proxy:8888"
    # Y el nombre viejo del ATRIBUTO sigue resolviendo a lo mismo: es lo que
    # leen hoy `cortex/tools.py` y `workers/cortex_curiosity.py`.
    assert cfg.cortex_egress_proxy_url == cfg.egress_proxy_url


def test_el_nombre_nuevo_gana_cuando_conviven_los_dos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Durante la transición el compose lleva los dos; el orden no puede ser azar."""
    cfg = _settings(
        monkeypatch,
        **{_NUEVO: "http://nuevo:8888", _VIEJO: "http://viejo:8888"},
    )

    assert cfg.egress_proxy_url == "http://nuevo:8888"


def test_sin_entorno_el_default_no_cambia(monkeypatch: pytest.MonkeyPatch) -> None:
    """El default sigue siendo el de dev (api-server FUERA de docker, puerto
    publicado al host por `docker-compose.dev.yml`). Cambiarlo aquí arreglaría
    el contenedor y rompería el desarrollo local; lo que arregla el contenedor
    es el generador del instalador, no este default."""
    cfg = _settings(monkeypatch)

    assert cfg.egress_proxy_url == "http://localhost:8888"
    assert cfg.cortex_egress_proxy_url == "http://localhost:8888"
