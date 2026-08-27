"""Guarda del artefacto de arranque descargable: el fichero que se LEE antes de correrlo.

El ADR 0161 se firmó con la opción **D envuelta en B**: el instalador *genera* el
árbol de arranque y no aprovisiona (D), y el artefacto de entrada del camino sin
clon es un **fichero compose descargable y auditable antes de ejecutarlo** (B),
no un ``curl | bash``. Este módulo vigila que ese fichero siga siendo lo que
justifica las dos mitades de esa decisión.

## Por qué un compose se guarda desde ``tests/docs/``

Porque aquí el artefacto **es** el documento. Su función no es sólo levantar un
contenedor: es que un operador que no ha clonado nada pueda leer en treinta
segundos qué se va a bajar y qué se le va a montar, y decidir. Un compose que
deja de ser legible o que gana un montaje de más no falla: **sigue funcionando**,
y lo que se pierde —la posibilidad de auditarlo— no la echa en falta ninguna
suite. Es el modo de fallo del §4 de ``docs/03-guides/verificar-antes-de-implementar.md``:
ni error, ni aviso, sólo una garantía que ya no está.

## Las cuatro cosas que se afirman, y por qué cada una

* **Ni el socket de Docker ni un atajo hacia él.** Es el punto entero de la
  opción D. El ADR 0060 (`docs/05-architecture-decisions/`,
  `0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md`)
  rechazó por escrito montar el socket en un contenedor —*escape a root*— y el
  socket-proxy no es la salida: su ACL deniega ``VOLUMES``, que es justo lo que
  el instalador necesita. El día que alguien añada aquí un ``docker.sock``, un
  ``DOCKER_HOST`` o un ``privileged: true``, la garantía se pierde **en
  silencio**: el fichero seguiría levantando el contenedor igual de bien.
* **Sólo se monta la raíz de datos (y el ``install.yaml`` en solo lectura).** Un
  montaje de más en el contenedor que mintea el material de arranque en claro no
  se nota en el resultado: se nota en lo que ese contenedor podría haber leído.
* **Ni un ``build:``.** El camino (1) existe para quien no tiene el repositorio.
  Un contexto de build convierte el artefacto en algo que sólo funciona dentro de
  un checkout, que es exactamente el hecho con el que abre el ADR 0161.
* **Hueco para el digest.** El tag es mutable: quien descargue este fichero se
  lleva «lo que ghcr sirva hoy bajo ese nombre». El patrón de la casa es el del
  [ADR 0148](../../docs/05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md)
  (``runtime_images.json``: digests escritos por el pipeline, nunca a mano), y
  aquí se replica dejando el hueco visible.

## La guarda que se retira sola

Mientras el hueco del digest esté **vacío** no hay imagen publicada, y entonces
las guías tienen que decir que el camino (1) **no está disponible**. El día que
el pipeline escriba el digest, esta guarda invierte la exigencia y pide que las
guías dejen de decirlo. Así el estado documentado no puede quedarse atrás del
estado real sin que algo se ponga rojo — que es lo que le pasó al README del
instalador, afirmando durante meses que aprovisionaba un stack de verdad.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: El artefacto descargable del camino sin clon.
_ARTIFACT = _REPO_ROOT / "docker" / "bootstrap" / "docker-compose.generate.yml"

_GETTING_STARTED = _REPO_ROOT / "docs" / "02-getting-started" / "01-installation.md"
_REFERENCE = _REPO_ROOT / "docs" / "04-reference" / "installation.md"
_RELEASE_RUNBOOK = _REPO_ROOT / "docs" / "06-runbooks" / "09-release.md"

#: Las dos guías que le enseñan a alguien los tres caminos de instalación.
_GUIDES = (_GETTING_STARTED, _REFERENCE)

#: Cómo se nombra el socket del daemon, en cualquiera de sus formas.
_SOCKET = re.compile(r"docker\.sock|/var/run/docker|docker-socket-proxy|tcp://.*:2375")

#: Un digest ya fijado por el pipeline.
_PINNED_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")

#: El hueco que el pipeline rellena: `${…DIGEST…:-}` con default vacío.
_DIGEST_SLOT = re.compile(r"\$\{[A-Za-z0-9_]*DIGEST[A-Za-z0-9_]*:-\}")

#: Fuentes de bind admitidas: la raíz de datos y el `install.yaml` del operador.
_DATA_ROOT_SOURCE = re.compile(r"AGENT_PLATFORM_DATA_ROOT|^/data/agent-platform$")
_CONFIG_SOURCE = re.compile(r"install\.ya?ml$")

#: La fila del camino (1) en la tabla de los tres caminos de cada guía.
_ROW_PATH_ONE = re.compile(r"^\|\s*\*{0,2}\(1\).*$", re.MULTILINE)
_UNAVAILABLE = re.compile(r"no\s+disponible", re.IGNORECASE)


def _doc_id(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


@pytest.fixture(scope="module")
def artifact_text() -> str:
    assert _ARTIFACT.is_file(), (
        f"falta el artefacto de arranque descargable ({_doc_id(_ARTIFACT)}). Es el "
        "envoltorio B del ADR 0161: sin él, el camino sin clon sólo puede ofrecerse "
        "como un `curl | bash`, que es el estándar más bajo de la casa"
    )
    return _ARTIFACT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def artifact(artifact_text: str) -> dict[str, Any]:
    parsed = yaml.safe_load(artifact_text)
    assert isinstance(parsed, dict), f"{_doc_id(_ARTIFACT)} no es un compose válido"
    return parsed


@pytest.fixture(scope="module")
def services(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found = artifact.get("services") or {}
    assert found, f"{_doc_id(_ARTIFACT)} no declara servicios: la guarda pasaría en vacío"
    return {str(name): svc for name, svc in found.items() if isinstance(svc, dict)}


def _binds(service: dict[str, Any]) -> list[str]:
    """Fuentes de los montajes del servicio, en sintaxis corta o larga."""
    sources: list[str] = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, str):
            sources.append(volume.split(":", 1)[0])
        elif isinstance(volume, dict):
            sources.append(str(volume.get("source", "")))
    return sources


# --- la garantía de la opción D: este contenedor no habla con el daemon -----


def test_the_artifact_never_reaches_the_docker_daemon(
    artifact: dict[str, Any], services: dict[str, dict[str, Any]]
) -> None:
    """Ni el socket, ni un proxy hacia él, ni privilegios para pedirlo prestado.

    Se afirma sobre el YAML **parseado**, no sobre el texto: la cabecera del
    fichero nombra el socket a propósito, para explicar por qué no está. Una
    guarda por subcadena obligaría a borrar esa explicación, que es la mitad que
    impide que alguien lo añada «porque faltaba».
    """
    offenders: list[str] = []
    for name, service in services.items():
        for source in _binds(service):
            if _SOCKET.search(source):
                offenders.append(f"{name}: monta {source!r}")
        environment = service.get("environment") or {}
        pairs = (
            environment.items()
            if isinstance(environment, dict)
            else ((item.split("=", 1)[0], item.split("=", 1)[-1]) for item in environment)
        )
        for key, value in pairs:
            if str(key) == "DOCKER_HOST" or _SOCKET.search(str(value)):
                offenders.append(f"{name}: {key}={value}")
        if service.get("privileged"):
            offenders.append(f"{name}: privileged: true")
        if service.get("group_add"):
            offenders.append(f"{name}: group_add {service['group_add']}")

    assert not offenders, (
        f"{_doc_id(_ARTIFACT)} le da acceso al daemon de Docker:\n  "
        + "\n  ".join(offenders)
        + "\nEso es acceso root efectivo al host y es la alternativa que el ADR 0060 "
        "rechazó por escrito. El instalador GENERA y no aprovisiona (ADR 0161, "
        "opción D): el `docker compose up` lo ejecuta el operador, fuera de aquí"
    )
    assert not _SOCKET.search(str(artifact.get("volumes") or "")), (
        f"{_doc_id(_ARTIFACT)} declara un volumen de nivel superior hacia el socket"
    )


def test_the_artifact_mounts_only_the_data_root_and_the_config(
    services: dict[str, dict[str, Any]],
) -> None:
    """Nada del host salvo lo que hay que escribir y lo que hay que leer."""
    seen_data_root = False
    seen_config = False
    unexpected: list[str] = []
    for name, service in services.items():
        for source in _binds(service):
            if _DATA_ROOT_SOURCE.search(source):
                seen_data_root = True
            elif _CONFIG_SOURCE.search(source):
                seen_config = True
            else:
                unexpected.append(f"{name}: {source!r}")

    assert not unexpected, (
        f"{_doc_id(_ARTIFACT)} monta cosas del host que no son ni la raíz de datos "
        "ni el `install.yaml`:\n  " + "\n  ".join(unexpected)
    )
    assert seen_data_root, (
        f"{_doc_id(_ARTIFACT)} no monta la raíz de datos: el contenedor escribiría el "
        "árbol de arranque en su propia capa efímera y saldría con rc=0 dejando el "
        "host vacío — el fallo que no avisa donde está la causa"
    )
    assert seen_config, (
        f"{_doc_id(_ARTIFACT)} no monta ningún `install.yaml`: sin config no hay nada que generar"
    )


def test_the_artifact_needs_no_repository(services: dict[str, dict[str, Any]]) -> None:
    """Un `build:` devolvería el camino (1) al clon, que es lo que se quería evitar."""
    for name, service in services.items():
        assert "build" not in service, (
            f"{_doc_id(_ARTIFACT)}: el servicio `{name}` declara `build:`. Este fichero "
            "se descarga suelto en una máquina sin checkout: un contexto de build no "
            "existe ahí. Es el hecho con el que abre el ADR 0161"
        )
        assert service.get("image"), (
            f"{_doc_id(_ARTIFACT)}: el servicio `{name}` no declara `image:` — sin "
            "`build` y sin `image`, compose aborta el proyecto entero"
        )


def test_the_artifact_stays_offline(services: dict[str, dict[str, Any]]) -> None:
    """Generar es escribir ficheros: no necesita red, y este contenedor mintea en
    claro el material de arranque del stack.

    Si algún día `generate` necesitase salida, el cambio se argumenta en el propio
    fichero y aquí — no se ajusta de pasada.
    """
    for name, service in services.items():
        assert service.get("network_mode") == "none", (
            f"{_doc_id(_ARTIFACT)}: el servicio `{name}` no declara "
            "`network_mode: none`. La generación es puramente de disco, y negarle la "
            "red al contenedor que produce los secretos de arranque es la contención "
            "más barata que existe"
        )


def test_the_image_is_the_published_installer_with_room_for_its_digest(
    services: dict[str, dict[str, Any]],
) -> None:
    """Tag para leerlo, digest para creerlo (ADR 0148 aplicado al instalador)."""
    for name, service in services.items():
        image = str(service["image"])
        assert "ghcr.io/daycry/installer" in image, (
            f"{_doc_id(_ARTIFACT)}: el servicio `{name}` levanta {image!r}, que no es "
            "la imagen publicada del instalador"
        )
        assert _PINNED_DIGEST.search(image) or _DIGEST_SLOT.search(image), (
            f"{_doc_id(_ARTIFACT)}: {image!r} va por tag y sin hueco para el digest. "
            "El tag es mutable: quien descargue este fichero se lleva lo que el "
            "registro sirva ese día bajo ese nombre. El patrón está en el ADR 0148 y "
            "en runtime_images.json — el digest lo escribe el pipeline, nunca a mano"
        )


def test_the_artifact_explains_why_the_socket_is_absent(artifact_text: str) -> None:
    """Una ausencia sin explicación se lee como un olvido, y se «arregla».

    Es literalmente lo que le pasó al instalador: la «Fase B» prometía por escrito
    montar el socket en dos sitios distintos, y esa promesa apunta al patrón que el
    ADR 0060 había rechazado.
    """
    assert _SOCKET.search(artifact_text), (
        f"{_doc_id(_ARTIFACT)} ya no nombra el socket de Docker. La cabecera tiene que "
        "decir que NO está y por qué: sin eso, el siguiente que lea el fichero verá un "
        "hueco en vez de una decisión"
    )
    assert "0060" in artifact_text, (
        f"{_doc_id(_ARTIFACT)} no cita el ADR 0060, que es el que prohíbe el socket"
    )
    assert "0161" in artifact_text, (
        f"{_doc_id(_ARTIFACT)} no cita el ADR 0161, que es el que decide que este "
        "fichero exista y que el instalador genere en vez de aprovisionar"
    )


def test_the_artifact_is_readable_in_thirty_seconds(artifact_text: str) -> None:
    """Su función es que alguien lo lea antes de ejecutarlo. Si no cabe, no se lee.

    El listón no es estético: un artefacto de arranque que crece hasta el tamaño de
    un compose de stack deja de auditarse y pasa a ejecutarse a ciegas, que es
    justo la diferencia con un `curl | bash`.
    """
    lines = artifact_text.splitlines()
    assert len(lines) <= 130, (
        f"{_doc_id(_ARTIFACT)} tiene {len(lines)} líneas. El artefacto del camino sin "
        "clon se lee antes de ejecutarse: lo que no quepa en una pantalla larga va a "
        "la guía de instalación, no aquí"
    )


# --- lo que las guías tienen que decir mientras no haya imagen publicada ----


@pytest.mark.parametrize("doc", _GUIDES, ids=_doc_id)
def test_every_guide_lists_the_three_paths(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    missing = [marker for marker in ("(1)", "(2)", "(3)") if marker not in text]
    assert not missing, (
        f"{_doc_id(doc)} no enumera los tres caminos de instalación (faltan {missing}). "
        "El ADR 0161 mide que los tres existen con estados distintos: decir sólo uno "
        "deja al lector eligiendo a ciegas"
    )
    assert "0161" in text, (
        f"{_doc_id(doc)} describe los caminos de instalación sin citar el ADR 0161, que "
        "es donde está medido el estado real de cada uno"
    )


@pytest.mark.parametrize("doc", _GUIDES, ids=_doc_id)
def test_the_guides_do_not_promise_an_unpublished_path(doc: Path, artifact_text: str) -> None:
    """El estado documentado del camino (1) sigue al digest del artefacto.

    Mientras el hueco esté vacío no hay imagen publicada y el camino (1) **no
    existe todavía**; en cuanto el pipeline escriba el digest, esta guarda exige lo
    contrario. El repo acaba de pagar caro el defecto opuesto: un README afirmando
    durante meses que el wizard aprovisionaba un stack real cuando simula.
    """
    text = doc.read_text(encoding="utf-8")
    row = _ROW_PATH_ONE.search(text)
    assert row is not None, (
        f"{_doc_id(doc)} no tiene una fila de tabla para el camino (1): esta guarda "
        "afirmaría sobre nada"
    )
    published = bool(_PINNED_DIGEST.search(artifact_text))
    if published:
        assert not _UNAVAILABLE.search(row.group(0)), (
            f"{_doc_id(doc)} sigue diciendo que el camino sin clon no está disponible, "
            "pero el artefacto ya va pineado por digest: la imagen está publicada y la "
            "guía se ha quedado atrás"
        )
    else:
        assert _UNAVAILABLE.search(row.group(0)), (
            f"{_doc_id(doc)} ofrece el camino sin clon sin decir que hoy NO está "
            "disponible. No hay imagen del instalador publicada (el artefacto no lleva "
            "digest), así que el `docker compose` de esa fila termina en `denied`"
        )


@pytest.mark.parametrize("doc", _GUIDES, ids=_doc_id)
def test_every_guide_points_at_the_downloadable_artifact(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    assert _ARTIFACT.name in text, (
        f"{_doc_id(doc)} describe el camino sin clon sin nombrar el fichero que hay que "
        f"descargar ({_ARTIFACT.name}): el envoltorio B del ADR 0161 es exactamente ese "
        "fichero, y sin su nombre el camino vuelve a ser una línea mágica"
    )


# --- el runbook de release: el orden duro ----------------------------------


def test_the_release_runbook_covers_the_installer_image() -> None:
    text = _RELEASE_RUNBOOK.read_text(encoding="utf-8")
    assert "installer" in text.lower(), (
        f"{_doc_id(_RELEASE_RUNBOOK)} no dice cómo se publica la imagen del instalador: "
        "el camino sin clon depende de que exista, y publicarla es un acto del operador"
    )
    assert _ARTIFACT.name in text, (
        f"{_doc_id(_RELEASE_RUNBOOK)} no nombra el artefacto descargable "
        f"({_ARTIFACT.name}): su digest lo escribe la release, así que quien publica "
        "tiene que saber que ese fichero forma parte de lo que publica"
    )


def test_the_release_runbook_writes_down_the_hard_order() -> None:
    """Publicar el instalador antes que las seis pineadas mueve el eslabón débil.

    Un instalador verificado que descarga seis imágenes sin verificar no es una
    cadena más fuerte: es la misma cadena con un eslabón más caro de auditar.
    """
    text = _RELEASE_RUNBOOK.read_text(encoding="utf-8")
    assert re.search(r"orden\s+duro", text, re.IGNORECASE), (
        f"{_doc_id(_RELEASE_RUNBOOK)} no tiene la sección del orden duro: las seis "
        "imágenes de plataforma pineadas por digest ANTES de publicar el instalador"
    )
    assert "digest" in text.lower() and "0161" in text, (
        f"{_doc_id(_RELEASE_RUNBOOK)} enuncia el orden sin anclarlo: falta el digest o "
        "la cita al ADR 0161 que lo decide"
    )
