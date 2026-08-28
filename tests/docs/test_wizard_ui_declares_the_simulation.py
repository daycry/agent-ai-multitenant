"""La pantalla del wizard tiene que decir que es una simulación — o no decir que instaló.

Hermana de :mod:`tests.docs.test_installer_docs_match_the_installer`, que vigila
los DOCUMENTOS. Ésta vigila la única superficie que el operador mira de verdad:
la interfaz.

## El defecto, medido

`grep -iE 'simulaci|simulation|demo|fake'` sobre `apps/installer/app/` y
`apps/installer/lib/` daba **cero resultados** el 2026-08-28, mientras el
backend servía `FakeStepExecutor` por defecto. La consecuencia en pantalla:
«Estamos aprovisionando el stack», luego «Instalación completada», luego «La
plataforma está instalada» sobre un usuario admin, una contraseña, un root token
de Vault y cinco unseal keys fabricadas con `secrets.token_urlsafe`, bajo el
aviso de que se muestran una sola vez y no hay forma de recuperarlas.

Toda la honestidad del sistema vivía en un docstring de Python, un comentario de
YAML, dos README y un runbook. Ninguna letra llegaba a la pantalla.

## Por qué una guarda de texto, y no un test de render

Podría comprobarse renderizando con Playwright, y de hecho hay seis specs. Pero
las seis **interceptan las rutas del backend**, así que ninguna se enteró del
fake: un test que mockea la fuente de la verdad no puede detectar que la fuente
miente. Lo que hay que fijar aquí es más simple y más duro de romper por
descuido: que las frases que afirman una instalación terminada **no puedan
existir fuera de una rama que consulta el modo**.

## Las dos mitades

**Positiva** — el aviso existe: hay un componente de simulación, el shell lo
monta, y las pantallas del tramo final (prerequisitos, instalación, credenciales)
lo consultan.

**Negativa** — ninguna de esas pantallas puede afirmar que hay algo instalado sin
mirar antes el modo. No se prohíbe la frase (el día que el ejecutor sea real hay
que poder decirla): se exige que quien la diga sepa si es verdad.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "apps" / "installer" / "app"

_NOTICE = _APP / "simulation-notice.tsx"
_SHELL = _APP / "wizard-shell.tsx"
_WELCOME = _APP / "step-panel.tsx"
_PREREQS = _APP / "prereq-panel.tsx"
_INSTALL = _APP / "steps" / "install-step.tsx"
_DONE = _APP / "steps" / "done-step.tsx"
_COMPOSE = _REPO_ROOT / "apps" / "installer" / "docker-compose.installer.yml"

#: Las pantallas que un operador recorre creyendo que instala. Cada una tiene que
#: poder distinguir «terminó» de «terminó la simulación».
_MODE_AWARE = (_WELCOME, _PREREQS, _INSTALL, _DONE)

#: Frases que afirman que hay una plataforma instalada. No están prohibidas: lo
#: que se exige es que el fichero que las contiene consulte el modo.
_CLAIMS_INSTALLED = re.compile(
    r"Instalación completada|plataforma está instalada|aprovisionando el stack",
    re.IGNORECASE,
)

#: Cómo se consulta el modo. Un fichero que la use está leyendo `/api/mode`.
_READS_THE_MODE = re.compile(r"useInstallerMode|mode\.simulated")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


# --- mitad positiva: el aviso existe y está montado ------------------------


def test_the_wizard_ships_a_simulation_notice_component() -> None:
    assert _NOTICE.is_file(), (
        f"{_rel(_NOTICE)} no existe: sin un sitio donde vivan el banner y el "
        "diálogo bloqueante, el aviso vuelve a repartirse por seis componentes "
        "y desaparece en el primer refactor"
    )
    text = _read(_NOTICE)
    for exportado in ("SimulationBanner", "SimulationGateDialog", "useInstallerMode"):
        assert exportado in text, f"{_rel(_NOTICE)} ya no exporta {exportado}"


def test_the_notice_defaults_to_warning_when_the_backend_does_not_answer() -> None:
    """Si `/api/mode` falla, se asume simulación. La asimetría no está repartida.

    Equivocarse avisando de más deja a un operador molesto; equivocarse avisando
    de menos le deja apuntando cinco unseal keys que no abren nada. Un
    `useState<InstallerMode>({simulated: false})` volvería a esconder el aviso
    justo cuando el backend no está para desmentirlo.
    """
    text = _read(_NOTICE)
    assert "ASSUMED_SIMULATION" in text, (
        f"{_rel(_NOTICE)} ya no declara el modo asumido por defecto: comprueba "
        "que un fallo de `/api/mode` sigue avisando en vez de callar"
    )
    bloque = text.split("ASSUMED_SIMULATION", 1)[1].split("}", 1)[0]
    assert re.search(r"simulated:\s*true", bloque), (
        "el modo asumido cuando `/api/mode` no responde ya no es «simulación»: "
        "un error de red no puede convertir «no lo sé» en «es una instalación real»"
    )


def test_the_shell_mounts_the_permanent_banner_and_the_blocking_dialog() -> None:
    """El banner va en el SHELL: se ve en los nueve pasos, no sólo en el primero.

    Y el diálogo también, porque el botón «Instalar» vive en el footer del shell:
    interponerse ahí es lo que lo hace imposible de saltar.
    """
    text = _read(_SHELL)
    assert "<SimulationBanner" in text, (
        f"{_rel(_SHELL)} ya no monta el banner permanente. Un aviso que sólo "
        "aparece en un paso lo pierde quien entra por un enlace o vuelve atrás"
    )
    assert "SimulationGateDialog" in text, (
        f"{_rel(_SHELL)} ya no interpone el diálogo bloqueante antes de «Instalar»"
    )


@pytest.mark.parametrize("screen", _MODE_AWARE, ids=_rel)
def test_every_screen_of_the_install_run_knows_whether_it_is_simulated(screen: Path) -> None:
    assert _READS_THE_MODE.search(_read(screen)), (
        f"{_rel(screen)} pinta una pantalla del recorrido de instalación sin "
        "consultar `/api/mode`: no puede distinguir «instalado» de «simulado», "
        "que es exactamente lo que pasaba antes del 2026-08-28"
    )


# --- mitad negativa: nadie canta victoria a ciegas -------------------------


@pytest.mark.parametrize("screen", _MODE_AWARE, ids=_rel)
def test_no_screen_claims_an_install_finished_without_checking_the_mode(screen: Path) -> None:
    text = _read(screen)
    claims = sorted({m.group(0) for m in _CLAIMS_INSTALLED.finditer(text)})
    if not claims:
        return
    assert _READS_THE_MODE.search(text), (
        f"{_rel(screen)} afirma {claims} sin mirar el modo. Esas frases no están "
        "prohibidas —el día que el ejecutor sea real hay que poder decirlas—, "
        "pero quien las diga tiene que saber si son verdad"
    )


def test_the_wizard_ui_mentions_the_simulation_somewhere_a_human_reads() -> None:
    """La comprobación literal que la auditoría hizo y salió a cero.

    Se mira el texto que se PINTA, no sólo los comentarios: un aviso escrito en
    un `/* */` es exactamente el defecto que se está corrigiendo.
    """
    pintado: list[str] = []
    for screen in (_NOTICE, *_MODE_AWARE):
        for raw in _read(screen).splitlines():
            line = raw.strip()
            if line.startswith(("//", "*", "/*")):
                continue
            if re.search(r"[Ss]imulaci", line):
                pintado.append(f"{_rel(screen)}: {line[:80]}")
    assert pintado, (
        "ninguna línea NO comentada de la UI del instalador menciona la "
        "simulación. Es el grep que la auditoría del 2026-08-28 corrió sobre "
        "apps/installer/app/ y apps/installer/lib/, con cero resultados, "
        "mientras la pantalla decía «Instalación completada»"
    )


# --- cómo se expone: el compose que sirve este wizard ---------------------


def test_the_bootstrap_compose_asks_for_the_simulation_explicitly() -> None:
    """Levantar el wizard tiene que ser un acto consciente, y constar en el YAML.

    Desde el 2026-08-28 `/api/install/stream` y `/api/finalize/reveal` responden
    `501` con seams de simulación salvo que `INSTALLER_ALLOW_SIMULATION` esté
    encendida. Este compose la enciende —para eso existe: revisar el flujo— pero
    la declara **en el fichero**, de modo que un `docker compose config` la
    enseñe y nadie pueda levantar la fachada sin verlo escrito.
    """
    data = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    backend = data["services"]["installer-backend"]
    entorno = backend.get("environment") or {}
    assert str(entorno.get("INSTALLER_ALLOW_SIMULATION", "")).strip('"') in {
        "1",
        "true",
        "yes",
        "on",
    }, (
        f"{_rel(_COMPOSE)} no declara `INSTALLER_ALLOW_SIMULATION`. Sin ella el "
        "wizard sirve los pasos 1-7 y devuelve 501 en los dos que fingen, que es "
        "lo correcto por defecto — pero entonces este compose no sirve ni para "
        "lo único que sirve. Si se quita a propósito, quita también este test"
    )


@pytest.mark.parametrize("service", ["installer-backend", "installer-ui"])
def test_the_bootstrap_compose_publishes_only_on_loopback(service: str) -> None:
    """Un servicio sin auth no se publica en todas las interfaces de un host.

    El backend del instalador no tiene autenticación de ningún tipo y su CORS es
    `allow_origins=["*"]`. Publicarlo como `"8080:8080"` lo expone en TODA la
    red del host. Hoy detrás sólo hay una simulación —el daño está acotado—, pero
    el ADR 0161 ya nombró ese riesgo al descartar su opción A: «arrastra el
    wizard sin auth con 8080/3100 en 0.0.0.0 si se distribuye tal cual».
    """
    data = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    puertos = [str(p) for p in (data["services"][service].get("ports") or [])]
    assert puertos, f"{_rel(_COMPOSE)}: el servicio `{service}` ya no publica puertos"
    sueltos = [p for p in puertos if not p.startswith(("127.0.0.1:", "localhost:"))]
    assert not sueltos, (
        f"{_rel(_COMPOSE)}: `{service}` publica {sueltos} en todas las interfaces "
        "del host. Es un servicio sin autenticación: acótalo a loopback "
        "(`127.0.0.1:PUERTO:PUERTO`) o justifica por qué deja de serlo"
    )


def test_the_stream_body_is_stripped_of_secrets_before_it_leaves_the_browser() -> None:
    """`toWireConfig` lleva los secretos; el stream no puede llevarlos.

    Los lleva a propósito —el MISMO objeto se postea a `/api/config/validate`,
    que los necesita para responder los `*_set`—, así que el sitio donde se
    quitan es justo antes de `install.start()`. El backend además los rechaza con
    un 400: esto es el cinturón, aquello los tirantes.
    """
    text = _read(_INSTALL)
    assert "stripSecrets" in text and "stripSecrets(toWireConfig(" in text, (
        f"{_rel(_INSTALL)} vuelve a postear `toWireConfig(config)` tal cual a "
        "`/api/install/stream`: eso mete minio_secret_key y los oauth_token de "
        "proveedor en el cuerpo, que es lo que tres docstrings juraban que no pasaba"
    )
    for campo in (
        "storage.minio_secret_key",
        "providers.claude_sdk.oauth_token",
        "providers.copilot.oauth_token",
        "providers.azure_foundry.api_key",
    ):
        assert campo in text, (
            f"{_rel(_INSTALL)} ya no quita `{campo}` del cuerpo del stream. Si el "
            "campo desapareció del modelo, quítalo también de aquí; si sigue "
            "existiendo, el backend responderá 400 nombrándolo"
        )
