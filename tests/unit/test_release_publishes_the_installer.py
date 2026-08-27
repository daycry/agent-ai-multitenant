"""La séptima imagen —el instalador— y el orden duro del ADR 0161.

`release-images.yml` publicaba seis imágenes y ninguna era la del instalador, así
que **instalar exigía clonar el repositorio** aunque la plataforma viniera de un
registry (ADR 0161 §«El hecho que lo motiva»). Este fichero fija las dos cosas
que hacen que añadir la séptima no empeore la cadena de suministro:

**El orden duro.** El operador firmó el 2026-08-27 que el instalador NO se
publica antes de que las seis de plataforma se puedan pinear por digest. Un
instalador verificado que descarga seis imágenes sin verificar no arregla nada:
mueve el eslabón débil un paso. Aquí ese orden **no es documentación**: el job
del instalador depende del que resuelve los digests, así que no hay forma de
publicarlo antes.

**La mitigación del Trivy post-push.** En este workflow Trivy corre DESPUÉS del
`push` —está declarado en el propio fichero desde prod-11 y no se cambia aquí—,
de modo que un rojo NO impide la publicación: la imagen ya está en el registro.
Lo que sí impide es lo que viene detrás. Como en
`build-runtime-templates.yml:224-227`, el job que resuelve digests corre sólo si
los builds terminaron en verde, y el del instalador cuelga de él: **una CVE
HIGH/CRITICAL en cualquiera de las seis deja el instalador sin publicar y el
manifiesto sin tocar**, por mucho que los blobs estén subidos. Sin esa cadena de
`needs:`, el escaneo posterior al push sería una alarma sin freno.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release-images.yml"

#: Jobs que publican las seis imágenes de plataforma de hoy.
_PLATFORM_JOBS = ("api-server", "backend", "admin-panel")

#: Dockerfile del instalador: el BACKEND, que es donde vive el CLI. El wizard
#: HTTP (`apps/installer/Dockerfile`) es el que el ADR 0161 §«Lo que hay hoy»
#: describe como simulación; empaquetar ése sería publicar la fachada.
_INSTALLER_DOCKERFILE = "apps/installer/backend/Dockerfile"


def _load() -> dict[str, Any]:
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1: un `on:` pelado se parsea como el booleano True.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else [str(n) for n in needs]


def _jobs() -> dict[str, Any]:
    return _load()["jobs"]


def _job_building(dockerfile: str) -> tuple[str, dict[str, Any]]:
    for name, job in _jobs().items():
        for step in job.get("steps") or []:
            if str((step.get("with") or {}).get("file", "")) == dockerfile:
                return name, job
    raise AssertionError(
        f"ningún job de release-images.yml construye {dockerfile}. Sin imagen "
        "publicada del instalador, instalar sigue exigiendo clonar el repo."
    )


def _transitive_needs(name: str, jobs: dict[str, Any]) -> set[str]:
    seen: set[str] = set()
    pending = list(_needs(jobs[name]))
    while pending:
        current = pending.pop()
        if current in seen or current not in jobs:
            continue
        seen.add(current)
        pending.extend(_needs(jobs[current]))
    return seen


# ---------------------------------------------------------------------------
# La séptima imagen
# ---------------------------------------------------------------------------
def test_el_instalador_se_construye_y_se_publica() -> None:
    name, job = _job_building(_INSTALLER_DOCKERFILE)
    step = next(
        s
        for s in job["steps"]
        if str((s.get("with") or {}).get("file", "")) == _INSTALLER_DOCKERFILE
    )
    with_ = step["with"]
    assert with_.get("push") is True, f"{name} construye el instalador pero no lo publica"
    assert "/installer:" in str(with_.get("tags", "")), (
        f"{name} no etiqueta la imagen como `installer`; el ADR 0161 documenta "
        "`ghcr.io/<owner>/installer:<tag>` como la forma de arrancarla"
    )
    assert str(with_.get("context", "")) == "apps/installer/backend", (
        "el Dockerfile del backend hace `COPY pyproject.toml` y `COPY src` "
        "relativos a su propio directorio: con otro contexto el build falla"
    )


def test_trivy_mira_la_imagen_del_instalador() -> None:
    """Igual que las otras seis: HIGH/CRITICAL y `exit-code: 1`."""
    name, job = _job_building(_INSTALLER_DOCKERFILE)
    scans = [
        s.get("with") or {}
        for s in job["steps"]
        if "trivy-action" in str(s.get("uses", "")) and "installer" in str(s.get("with", {}))
    ]
    assert scans, f"{name} publica el instalador y nadie lo escanea"
    for with_ in scans:
        assert with_.get("severity") == "HIGH,CRITICAL"
        assert str(with_.get("exit-code")) == "1", (
            "un Trivy sin `exit-code: 1` informa y deja pasar: es el modo de "
            "fallo que hace creer que hay un gate donde sólo hay un informe"
        )


# ---------------------------------------------------------------------------
# El orden duro: primero los digests de las seis, después el instalador
# ---------------------------------------------------------------------------
def test_los_digests_se_resuelven_despues_de_publicar_las_seis() -> None:
    jobs = _jobs()
    name = _digest_job_name(jobs)
    faltan = [j for j in _PLATFORM_JOBS if j not in _needs(jobs[name])]
    assert not faltan, (
        f"`{name}` resuelve digests sin depender de {faltan}: resolvería contra "
        "la release anterior, o contra nada, y escribiría un manifiesto que "
        "miente sobre lo que se acaba de publicar"
    )


def test_el_instalador_no_se_publica_antes_que_los_digests() -> None:
    """El orden duro del ADR 0161, hecho mecánico.

    Resolver y construir viven en el MISMO job (ver
    `test_el_instalador_se_construye_con_los_digests_de_esta_release`), así que
    el orden no es un `needs:` sino el orden de los pasos: si el `push` fuera
    primero, se publicaría un instalador que pinea la release anterior — que es
    exactamente «un instalador verificado descargando imágenes sin verificar»,
    lo que el operador firmó que no debe pasar.
    """
    installer, job = _job_building(_INSTALLER_DOCKERFILE)
    steps = job["steps"]
    resuelve = next(
        i
        for i, s in enumerate(steps)
        if "installer_backend.platform_release" in str(s.get("run", ""))
    )
    construye = next(
        i
        for i, s in enumerate(steps)
        if str((s.get("with") or {}).get("file", "")) == _INSTALLER_DOCKERFILE
    )
    assert resuelve < construye, (
        f"en `{installer}` el build del instalador (paso {construye}) va antes de "
        f"resolver los digests (paso {resuelve}): la imagen publicada llevaría el "
        "manifiesto del checkout, o sea el de la release ANTERIOR, y encima lo "
        "parecería todo correcto."
    )


def test_una_cve_en_cualquiera_de_las_seis_deja_al_instalador_sin_publicar() -> None:
    """La única mitigación real del Trivy posterior al push.

    Trivy corre después del `push` (declarado en el workflow desde prod-11), así
    que un rojo no despublica nada. Lo que sí hace, si la cadena de `needs:`
    está completa, es dejar sin correr todo lo que viene detrás.
    """
    jobs = _jobs()
    installer, _ = _job_building(_INSTALLER_DOCKERFILE)
    alcance = _transitive_needs(installer, jobs)
    faltan = [j for j in _PLATFORM_JOBS if j not in alcance]
    assert not faltan, (
        f"`{installer}` puede publicarse aunque {faltan} hayan fallado (Trivy "
        "incluido). Con el escaneo posterior al push, esta cadena de `needs:` es "
        "lo ÚNICO que impide que una CVE HIGH/CRITICAL llegue al instalador."
    )


def test_ningun_job_se_perdona_a_si_mismo() -> None:
    """`continue-on-error` desharía en una línea toda la cadena de arriba."""
    culpables = []
    for name, job in _jobs().items():
        if job.get("continue-on-error"):
            culpables.append(name)
        for step in job.get("steps") or []:
            if step.get("continue-on-error"):
                culpables.append(f"{name}:{step.get('name', '?')}")
    assert not culpables, f"jobs/pasos que ignoran su propio fallo: {culpables}"


# ---------------------------------------------------------------------------
# El manifiesto lo escribe el pipeline, y la lista de apps la deriva
# ---------------------------------------------------------------------------
def _digest_job_name(jobs: dict[str, Any]) -> str:
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            if "installer_backend.platform_release" in str(step.get("run", "")):
                return name
    raise AssertionError(
        "ningún job invoca `installer_backend.platform_release`. Sin vía de "
        "refresco, un digest pineado es una CVE congelada para siempre (ADR "
        "0148, condición 1)."
    )


def test_la_lista_de_apps_del_job_se_deriva_del_modulo() -> None:
    """Enumerar las seis en el YAML repite el defecto del `watchdog`.

    Entró el 2026-08-02 y estuvo diez días sin publicarse porque los dos sitios
    que repartían apps llevaban la lista escrita a mano.
    """
    jobs = _jobs()
    script = "\n".join(
        str(step.get("run", "")) for step in jobs[_digest_job_name(jobs)].get("steps") or []
    )
    assert "PLATFORM_APPS" in script, (
        "el job enumera las apps en vez de leerlas de "
        "`installer_backend.platform_images.PLATFORM_APPS`: la séptima app que "
        "entre se quedará sin digest y nadie lo verá"
    )


def test_el_instalador_se_construye_con_los_digests_de_esta_release() -> None:
    """Si no, la imagen publicada lleva los digests de la release ANTERIOR.

    El manifiesto del árbol se escribe por PR, que se mergea después. Construir
    el instalador desde el checkout sin regenerarlo primero publica una imagen
    que pinea la versión pasada — y encima parecería correcta.
    """
    jobs = _jobs()
    installer, _ = _job_building(_INSTALLER_DOCKERFILE)
    digest_job = _digest_job_name(jobs)
    assert installer == digest_job, (
        "el instalador se construye en un job distinto del que resuelve los "
        f"digests (`{installer}` vs `{digest_job}`). Entre los dos habría que "
        "pasarse el manifiesto por artefacto; hacerlo en el mismo job es lo que "
        "garantiza que la imagen publicada pinea lo que se acaba de publicar."
    )


def test_el_artefacto_de_arranque_se_sella_con_el_digest_publicado() -> None:
    """`docker/bootstrap/…` lleva el hueco a la vista y lo rellena ESTE job.

    El runbook 09 §«Dónde acaba el digest» dice «lo rellena el pipeline al
    publicar, y nunca a mano». Sin el paso, esa frase es una promesa sin
    mecanismo y el fichero que la gente descarga va por **tag mutable**.

    El sellado tiene que ir DESPUÉS del `push`: el digest de una imagen no existe
    hasta que está publicada.
    """
    installer, job = _job_building(_INSTALLER_DOCKERFILE)
    steps = job["steps"]
    construye = next(
        i
        for i, s in enumerate(steps)
        if str((s.get("with") or {}).get("file", "")) == _INSTALLER_DOCKERFILE
    )
    sella = [i for i, s in enumerate(steps) if "--bootstrap" in str(s.get("run", ""))]
    assert sella, (
        f"`{installer}` publica el instalador y no sella "
        "`docker/bootstrap/docker-compose.generate.yml`: el artefacto descargable "
        "seguiría referenciando un tag mutable"
    )
    assert min(sella) > construye, (
        "se sella antes de publicar, y el digest de una imagen no existe hasta "
        "que está en el registro"
    )
    script = "\n".join(str(steps[i].get("run", "")) for i in sella)
    assert "--installer-digest" in script, (
        "el sellado no pasa el digest resuelto: sellar sin digest no sella nada"
    )


def test_solo_se_propone_el_manifiesto_para_un_tag_final() -> None:
    """Un `v1.0.0-rc1` no puede dejar `master` pineado a una release candidate.

    El workflow dispara con `tags: ['v*']`, que incluye las pre-releases. La
    imagen del instalador de esa ejecución sí debe pinear los digests del rc
    (coherencia interna), pero el PR contra `master` sólo tiene sentido para un
    tag final.
    """
    jobs = _jobs()
    script = "\n".join(
        str(step.get("run", "")) for step in jobs[_digest_job_name(jobs)].get("steps") or []
    )
    assert "gh pr create" in script, "el manifiesto no llega al repo por PR"
    assert "-rc" in script or "prerelease" in script or "[0-9]" in script, (
        "nada en el job distingue un tag final de una pre-release, así que un "
        "`v1.0.0-rc1` abriría un PR que pinea master a la release candidate"
    )
