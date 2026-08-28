"""El wizard HTTP dice lo que es, o no hace nada — ADR 0161 + auditoría 2026-08-28.

## El defecto que fija este fichero

Un operador levantaba `apps/installer/docker-compose.installer.yml`, abría
`http://host:3100` y recorría nueve pasos: prerequisitos en verde, barra de
progreso, log paso a paso, «Instalación completada. La plataforma está
instalada», y una pantalla final con usuario admin, contraseña, root token de
Vault y cinco unseal keys bajo el aviso «se muestran una sola vez y no hay forma
de recuperarlas». Las apuntaba en su gestor de contraseñas.

No había nada instalado. El ejecutor por defecto es
:class:`~installer_backend.install.FakeStepExecutor` y las credenciales las
fabrica ``secrets.token_urlsafe``. **Toda** la honestidad del sistema vivía en
sitios que ese operador no abrió: un docstring de Python, un comentario de YAML,
el README del instalador y el runbook. `grep -i 'simulaci|simulation|fake'` sobre
`apps/installer/app/` y `apps/installer/lib/` daba cero resultados.

Y no era sólo copy: `/api/install/stream` **recibía los secretos de verdad**
—`storage.minio_secret_key`, los `oauth_token` de Claude SDK y Copilot, la
`api_key` de Azure Foundry— mientras tres sitios distintos afirmaban por escrito
que ese cuerpo no llevaba secretos. Quien fuera a decidir el futuro del wizard
leía la afirmación falsa justo donde se diseña.

## La decisión, y por qué ésta y no otra

Tres salidas tenía esto, y las tres se midieron:

* **Cablear el ejecutor real** — prohibido por el ADR 0161 §Decisión: el
  contenedor *genera y no provisiona*, y no se le monta el socket de Docker. De
  los cinco pasos del pipeline sólo `generate_config` no necesita el daemon.
  Además el backend es deliberadamente sin estado, así que no hay dónde guardar
  el `InstallerConfig` entre el paso 6 y el 8; y habría que ponerle
  autenticación, porque hoy es un endpoint sin auth. 4-7 días **y un ADR nuevo**.
* **Retirarlo** — 2-3 días: 20 ficheros de `app/` + `lib/`, 6 specs Playwright,
  ~70 tests, y tres guardas de cadena de suministro que se apoyan en que la
  superficie npm exista (`NPM_SURFACES`, `assert seen >= 2`). Y tira lo único del
  wizard que SÍ es real: la captura y validación de config de los pasos 2-7.
* **Dejarlo detrás de un flag y diciendo lo que es** — lo que fija este fichero.

## Qué se afirma aquí

1. **Sin `INSTALLER_ALLOW_SIMULATION`, la simulación no corre.** Los dos
   endpoints que fingen —`/api/install/stream` y `/api/finalize/reveal`—
   responden `501` nombrando el CLI. Los pasos 2-7, que son reales, siguen
   sirviendo: la mitad honesta del wizard no se castiga por la deshonesta.
2. **Con el flag, la respuesta se declara simulada.** `simulated: true` viaja en
   `/api/mode`, `/api/prereqs`, `/api/finalize/status` y en el propio cuerpo del
   revelado, para que un cliente que sólo llame al revelado también se entere.
3. **El endpoint rechaza secretos.** No «no los registra»: los **rechaza** con
   `400` nombrando el campo y sin repetir el valor. Así la frase «este cuerpo no
   lleva secretos» pasa a ser cierta por construcción y no por buena voluntad del
   cliente, que es la diferencia entre un contrato y un comentario.
4. **La lista de campos secretos se DERIVA del modelo.** Se recorren los
   ``SecretStr`` de :class:`~installer_backend.config.InstallerConfig`. Una lista
   escrita a mano envejece el día que alguien añade un proveedor, y envejece en
   silencio: el campo nuevo viajaría y nadie se enteraría.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_FLAG = "INSTALLER_ALLOW_SIMULATION"

#: Un cuerpo de config con la forma que produce `toWireConfig` del wizard, con
#: los cuatro secretos que la auditoría midió viajando en claro.
_CONFIG_WITH_SECRETS: dict[str, object] = {
    "system": {"domain": "ejemplo.com", "environment": "production"},
    "storage": {
        "data_root": "/data/agent-platform",
        "minio_bucket": "agentic",
        "minio_access_key": "agentic",
        "minio_secret_key": "no-debe-viajar-por-el-stream",
    },
    "providers": {
        "claude_sdk": {"enabled": True, "oauth_token": "tampoco-este"},
        "ollama": {"enabled": False},
    },
    "tenant": {"tenant_name": "Acme", "admin_email": "admin@ejemplo.com"},
}


@pytest.fixture
def sin_flag(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """El modo por defecto: nadie ha pedido una simulación."""
    from installer_backend.main import create_app

    monkeypatch.delenv(_FLAG, raising=False)
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def con_flag(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """La simulación pedida a propósito, que es la única forma de tenerla.

    El `FinalizeService` por defecto es un singleton de PROCESO —el stream lo
    arma y una petición posterior lo consume, así que las dos tienen que ver la
    misma instancia—, de modo que un test que revele deja al siguiente sin poder
    armar. Cada test se lleva el suyo.
    """
    from installer_backend.finalize import FinalizeService
    from installer_backend.main import create_app, get_finalize_service
    from installer_backend.seams import StubInstallerLifecycle

    monkeypatch.setenv(_FLAG, "1")
    app = create_app()
    servicio = FinalizeService(lifecycle=StubInstallerLifecycle())
    app.dependency_overrides[get_finalize_service] = lambda: servicio
    with TestClient(app) as client:
        yield client


def _sse_stages(text: str) -> list[str]:
    return [
        json.loads(line[len("data:") :].strip())["stage"]
        for line in text.splitlines()
        if line.startswith("data:") and line[len("data:") :].strip()
    ]


# ---------------------------------------------------------------------------
# 1. Sin flag, la simulación no corre
# ---------------------------------------------------------------------------
def test_el_stream_no_simula_si_nadie_lo_ha_pedido(sin_flag: TestClient) -> None:
    resp = sin_flag.post("/api/install/stream", json={"config": {}})

    assert resp.status_code == 501, (
        "sin INSTALLER_ALLOW_SIMULATION el wizard sigue fingiendo una instalación: "
        "ése es exactamente el estado que la auditoría midió"
    )
    detail = resp.json()["detail"]
    assert "installer_backend.cli" in detail or "install.sh" in detail, (
        "el 501 tiene que decir cuál es el camino que SÍ instala; negarse sin dar "
        "salida es media corrección"
    )


def test_el_revelado_no_reparte_credenciales_falsas_por_defecto(sin_flag: TestClient) -> None:
    resp = sin_flag.post("/api/finalize/reveal")

    assert resp.status_code == 501, (
        "el revelado seguía sirviendo `secrets.token_urlsafe` con el mismo "
        "contrato que el camino real: un operador las apunta y no abren nada"
    )


def test_la_mitad_REAL_del_wizard_sigue_sirviendo_sin_flag(sin_flag: TestClient) -> None:
    """Los pasos 2-7 (captura + validación + preview) son reales y no se castigan.

    Si apagar la simulación apagase también esto, la opción elegida se
    convertiría en la de retirar el wizard por la puerta de atrás.
    """
    assert sin_flag.get("/healthz").status_code == 200
    assert sin_flag.get("/api/wizard/steps").status_code == 200
    assert sin_flag.get("/api/prereqs").status_code == 200
    assert sin_flag.get("/api/install/steps").status_code == 200

    validada = sin_flag.post(
        "/api/config/validate",
        json={
            "system": {"domain": "ejemplo.com", "environment": "production"},
            "resources": {
                "worker_replicas": 2,
                "worker_memory_gib": 4,
                "ollama_mode": "none",
                "embedding_model": "nomic-embed-text",
            },
            "storage": {
                "data_root": "/data/agent-platform",
                "minio_bucket": "agentic",
                "minio_access_key": "agentic",
                "minio_secret_key": "una-clave-larga",
            },
            "providers": {"ollama": {"enabled": True, "endpoint": "http://ollama:11434"}},
            "tenant": {"tenant_name": "Acme", "admin_email": "admin@ejemplo.com"},
        },
    )
    assert validada.status_code == 200, validada.text


@pytest.mark.parametrize("valor", ["", "0", "false", "no", "off"])
def test_un_valor_falso_no_enciende_la_simulacion(
    monkeypatch: pytest.MonkeyPatch, valor: str
) -> None:
    """`INSTALLER_ALLOW_SIMULATION=0` no puede leerse como «sí».

    Es el modo de fallo clásico de los flags por presencia: quien lo pone a `0`
    para apagarlo lo estaría encendiendo.
    """
    from installer_backend.main import create_app

    monkeypatch.setenv(_FLAG, valor)
    with TestClient(create_app()) as client:
        assert client.post("/api/install/stream", json={"config": {}}).status_code == 501


# ---------------------------------------------------------------------------
# 2. Con flag, la respuesta se declara simulada
# ---------------------------------------------------------------------------
def test_la_ruta_de_modo_dice_que_es_una_simulacion(sin_flag: TestClient) -> None:
    cuerpo = sin_flag.get("/api/mode").json()

    assert cuerpo["simulated"] is True
    assert cuerpo["install_enabled"] is False
    assert "install.sh" in cuerpo["real_path"] or "installer_backend.cli" in cuerpo["real_path"]
    # El aviso es el que pinta la UI: si viene vacío, la pantalla no puede avisar.
    assert cuerpo["notice_es"].strip()
    assert cuerpo["notice_en"].strip()


def test_con_el_flag_el_modo_sigue_diciendo_que_simula(con_flag: TestClient) -> None:
    cuerpo = con_flag.get("/api/mode").json()

    assert cuerpo["simulated"] is True, (
        "encender el flag no convierte la simulación en una instalación: sólo "
        "autoriza a correrla. Si esto pasa a False habrá que revisar la UI entera"
    )
    assert cuerpo["install_enabled"] is True


def test_los_prerequisitos_se_marcan_como_no_probados(sin_flag: TestClient) -> None:
    """El paso de prerequisitos pintaba UNA fila verde inventada.

    `StubPrereqChecker` devuelve «Installer scaffold ready» en OK, y `can_proceed`
    es `not any(blocking)` sobre esa única fila: en una máquina sin Docker, con 4
    GiB de RAM y los puertos 80/443 ocupados, el wizard abre la puerta igual.
    """
    cuerpo = sin_flag.get("/api/prereqs").json()

    assert cuerpo["simulated"] is True, (
        "la respuesta de prerequisitos no dice que las comprobaciones son un "
        "stub, así que la pantalla no tiene con qué avisar"
    )


def test_el_revelado_simulado_se_declara_en_su_propio_cuerpo(con_flag: TestClient) -> None:
    """`simulated` viaja en el revelado, no sólo en una ruta aparte.

    Un cliente que sólo llame a `/api/finalize/reveal` —o un curl— tiene que
    enterarse por el mismo cuerpo que le da las credenciales.
    """
    stream = con_flag.post("/api/install/stream", json={"config": {}})
    assert stream.status_code == 200, stream.text
    assert "done" in _sse_stages(stream.text)

    estado = con_flag.get("/api/finalize/status").json()
    assert estado["simulated"] is True

    revelado = con_flag.post("/api/finalize/reveal")
    assert revelado.status_code == 200, revelado.text
    cuerpo = revelado.json()
    assert cuerpo["simulated"] is True
    # Y el aviso que la UI pinta encima de las credenciales dice qué son.
    assert "simulaci" in cuerpo["warning_es"].lower()
    assert "simulat" in cuerpo["warning_en"].lower()


# ---------------------------------------------------------------------------
# 3. El endpoint RECHAZA secretos (la afirmación pasa a ser cierta)
# ---------------------------------------------------------------------------
def test_el_stream_rechaza_un_cuerpo_que_lleva_secretos(con_flag: TestClient) -> None:
    resp = con_flag.post("/api/install/stream", json={"config": _CONFIG_WITH_SECRETS})

    assert resp.status_code == 400, (
        "el stream aceptaba minio_secret_key y los oauth_token en claro mientras "
        "su propio docstring afirmaba que no viajaban por ahí"
    )
    detail = resp.json()["detail"]
    assert "storage.minio_secret_key" in detail
    assert "providers.claude_sdk.oauth_token" in detail
    # El rechazo nombra el CAMPO, nunca el valor: un 400 que repite el secreto lo
    # deja en el log de acceso del navegador, del proxy y de quien mire.
    assert "no-debe-viajar-por-el-stream" not in resp.text
    assert "tampoco-este" not in resp.text


def test_un_cuerpo_sin_secretos_pasa(con_flag: TestClient) -> None:
    """La guarda no puede ser un «no» a todo: el eco no-secreto sí viaja."""
    limpio = json.loads(json.dumps(_CONFIG_WITH_SECRETS))
    del limpio["storage"]["minio_secret_key"]
    del limpio["providers"]["claude_sdk"]["oauth_token"]

    resp = con_flag.post("/api/install/stream", json={"config": limpio})

    assert resp.status_code == 200, resp.text


def test_los_campos_secretos_se_derivan_del_modelo() -> None:
    """La lista no se escribe a mano: se lee de los `SecretStr` de la config.

    Una lista literal envejece el día que alguien añade un proveedor con
    credencial, y envejece **en silencio** — el campo nuevo viajaría y ninguna
    guarda se enteraría. Aquí se comprueban las dos mitades: que la derivación
    encuentra los que hay hoy, y que un `SecretStr` nuevo entraría solo.
    """
    from installer_backend.config import InstallerConfig
    from installer_backend.main import secret_field_paths

    paths = secret_field_paths(InstallerConfig)

    assert "storage.minio_secret_key" in paths
    assert "providers.claude_sdk.oauth_token" in paths
    assert "providers.copilot.oauth_token" in paths
    assert "providers.azure_foundry.api_key" in paths
    # Y NADA que no sea secreto: un falso positivo aquí bloquea una instalación
    # legítima con un 400 incomprensible.
    assert "storage.minio_access_key" not in paths
    assert "system.domain" not in paths


def test_el_registro_de_seams_simulados_no_puede_divergir_del_cli() -> None:
    """`main.py` y `cli.py` tienen que reconocer los MISMOS fakes.

    El CLI aborta con exit 4 si detecta uno sin `--dry-run`; el wizard marca la
    respuesta como simulada con la misma comprobación. Si las dos listas
    divergen, un fake nuevo sería «real» para uno de los dos caminos — y el que
    se lo tragaría en silencio es el wizard.
    """
    from installer_backend.cli import _SIMULATION_INSTALL_SEAMS
    from installer_backend.main import SIMULATION_SEAMS

    assert set(_SIMULATION_INSTALL_SEAMS).issubset(set(SIMULATION_SEAMS)), (
        "cli.py reconoce como simulación seams que main.py da por reales: "
        f"{sorted(t.__name__ for t in set(_SIMULATION_INSTALL_SEAMS) - set(SIMULATION_SEAMS))}"
    )
