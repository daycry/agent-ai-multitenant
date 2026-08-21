"""Los runbooks de DR tienen que decir la verdad (prod-04 task_prod_04_14).

Un runbook es CÓDIGO que ejecuta un humano bajo presión. Y a diferencia del
código, nada rompe cuando envejece: `docs/06-runbooks/dr-full-restore.md` mandó
durante meses `docker compose exec -T worker python -c ...` —un servicio que no
existe en ningún compose (se llama `workers`) y que además el propio restore
para— sin que ningún test se quejase. El fallo solo aparecía ejecutando el DR de
verdad, que es el peor momento posible.

Este módulo comprueba, sobre el TEXTO de los runbooks, que:

* no invocan servicios de compose inexistentes;
* los ficheros y símbolos de Python que citan existen;
* los enlaces relativos entre runbooks apuntan a ficheros reales;
* siguen diciendo lo que prod-04 corrigió (custodia de la clave, fail-stopped,
  RPO/RTO), para que un revert documental se note.

Cada comprobación lleva su aserción de «encontré algo»: una guarda que deja de
encontrar infractores porque dejó de mirar pasa en verde para siempre.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOKS = _REPO_ROOT / "docs" / "06-runbooks"

#: Los runbooks del dominio DR. Si se añade uno nuevo, entra aquí.
_DR_RUNBOOKS = (
    "04-disaster-recovery.md",
    "dr-full-restore.md",
    "dr-manual-backup.md",
    "dr-tenant-restore.md",
    "dr-drill.md",
)


def _read(name: str) -> str:
    path = _RUNBOOKS / name
    assert path.is_file(), f"falta el runbook {name}"
    return path.read_text(encoding="utf-8")


def _prose(name: str) -> str:
    """El texto con los saltos de línea colapsados.

    Los runbooks van a 80 columnas, así que una frase se parte por la mitad y un
    `in` literal falla por un salto de línea, no por que la frase falte. También
    se quita el `>` de las citas, que es lo mismo pero peor: parte la frase Y
    mete un carácter en medio. Afirmar sobre prosa exige normalizar antes."""
    lines = [
        line.lstrip().removeprefix("> ").removeprefix(">") for line in _read(name).splitlines()
    ]
    return " ".join(" ".join(lines).split())


def _all_declared_services() -> set[str]:
    """Servicios declarados en CUALQUIERA de los composes del repo + el generado.

    La unión, a propósito: un runbook puede hablar legítimamente del compose de
    dev, del de monitorización o del de producción.
    """
    names: set[str] = set()
    for compose in sorted((_REPO_ROOT / "docker").glob("docker-compose*.yml")):
        try:
            data = yaml.safe_load(compose.read_text(encoding="utf-8"))
        except yaml.YAMLError:  # pragma: no cover — un compose roto es otro test
            continue
        if isinstance(data, dict) and isinstance(data.get("services"), dict):
            names.update(str(n) for n in data["services"])
    from installer_backend.compose_generator import (
        CORE_SERVICES,
        MONITORING_SERVICES,
        VOICE_SERVICES,
    )

    names.update(CORE_SERVICES)
    names.update(MONITORING_SERVICES)
    names.update(VOICE_SERVICES)
    return names


# --------------------------------------------------------------------------- #
# Servicios de compose
# --------------------------------------------------------------------------- #

#: `docker compose ... exec|stop|start|restart|up|logs <servicio>`.
_COMPOSE_VERB_RE = re.compile(
    r"(?:^|\s)(?:exec|stop|start|restart|logs)\s+(?:-\w+\s+|--\w[\w-]*\s+)*([a-z][a-z0-9_-]*)",
    re.MULTILINE,
)


def test_the_dr_runbooks_never_name_a_service_that_does_not_exist() -> None:
    declared = _all_declared_services()
    assert len(declared) >= 15, f"no se pudieron leer los composes: {sorted(declared)}"

    offenders: list[str] = []
    seen = 0
    for name in _DR_RUNBOOKS:
        body = _read(name)
        for block in re.findall(r"```bash\n(.*?)```", body, re.DOTALL):
            if "docker compose" not in block:
                continue
            for candidate in _COMPOSE_VERB_RE.findall(block):
                seen += 1
                # Palabras que NO son nombres de servicio en esa posición.
                if candidate in {"-d", "detach", "vault", "postgres"} or candidate in declared:
                    continue
                offenders.append(f"{name}: {candidate!r}")
    assert seen >= 2, f"la guarda dejó de encontrar comandos de compose (vio {seen})"
    assert not offenders, (
        f"estos runbooks invocan servicios que no existen en ningún compose: {offenders}. "
        f"`docker compose <verbo> <desconocido>` devuelve != 0 y el procedimiento "
        f"se rompe en medio de un DR. Declarados: {sorted(declared)}"
    )


def test_the_phantom_worker_service_is_gone_from_the_dr_runbooks() -> None:
    """El caso concreto, explícito, para que un revert cuente una historia.

    Se permite mencionarlo en prosa (los runbooks explican POR QUÉ estaba mal);
    lo que no se permite es volver a invocarlo.
    """
    for name in _DR_RUNBOOKS:
        for line in _read(name).splitlines():
            stripped = line.strip()
            if stripped.startswith(("#", ">", "*", "-")) or "`exec -T worker`" in stripped:
                continue  # prosa explicativa
            assert "exec -T worker " not in line and not stripped.endswith("exec -T worker \\"), (
                f"{name} vuelve a invocar el servicio fantasma `worker` "
                f"(se llama `workers`, y además el restore lo para): {line!r}"
            )


# --------------------------------------------------------------------------- #
# Ficheros y símbolos citados
# --------------------------------------------------------------------------- #


#: Rutas citadas que NO están (ni deben estar) en el repo: las genera el
#: despliegue y llevan secretos. Citarlas es correcto; versionarlas, no.
_RUNTIME_ONLY_PATHS = {"docker/.env"}


def test_every_repo_path_the_dr_runbooks_cite_exists() -> None:
    path_re = re.compile(r"`((?:scripts|docker|apps|docs|tests)/[\w./-]+)`")
    offenders: list[str] = []
    seen = 0
    for name in _DR_RUNBOOKS:
        for cited in path_re.findall(_read(name)):
            seen += 1
            if cited in _RUNTIME_ONLY_PATHS:
                continue
            if not (_REPO_ROOT / cited).exists():
                offenders.append(f"{name}: {cited}")
    assert seen >= 3, f"la guarda dejó de encontrar rutas citadas (vio {seen})"
    assert not offenders, f"los runbooks citan rutas que no existen: {offenders}"


def test_every_python_entrypoint_the_dr_runbooks_cite_is_importable() -> None:
    """Los runbooks mandan ejecutar módulos y funciones concretos. Si un refactor
    los renombra, el operador se entera durante el DR."""
    import importlib

    expected = {
        "workers.backup": ("run_full_backup",),
        "workers.restore": ("run_full_restore", "RestoreError", "RestorePartialError"),
        "workers.restore_reconcile": ("reconcile_after_restore", "main"),
        "workers.backup_verification": ("verify_bundle",),
        "workers.backup_destinations": ("build_destination",),
        "workers.restore_per_tenant": ("run_per_tenant_restore", "confirmation_token"),
        "workers.backup_encryption": ("BackupEncryptor", "EnvSecretsProvider"),
    }
    text = "\n".join(_read(name) for name in _DR_RUNBOOKS)
    checked = 0
    for module_name, symbols in expected.items():
        short = module_name.split(".", 1)[1]
        if short not in text:
            continue
        checked += 1
        module = importlib.import_module(module_name)
        for symbol in symbols:
            assert hasattr(module, symbol), f"los runbooks citan {module_name}.{symbol} y no existe"
    assert checked >= 4, f"la guarda dejó de encontrar módulos citados (vio {checked})"


def test_relative_links_between_dr_runbooks_resolve() -> None:
    link_re = re.compile(r"\]\((\./[\w.-]+\.md)(?:#[\w-]+)?\)")
    offenders: list[str] = []
    seen = 0
    for name in _DR_RUNBOOKS:
        for target in link_re.findall(_read(name)):
            seen += 1
            if not (_RUNBOOKS / target[2:]).is_file():
                offenders.append(f"{name} -> {target}")
    assert seen >= 5, f"la guarda dejó de encontrar enlaces (vio {seen})"
    assert not offenders, f"enlaces rotos entre runbooks: {offenders}"


# --------------------------------------------------------------------------- #
# Lo que prod-04 corrigió tiene que seguir dicho
# --------------------------------------------------------------------------- #


def test_the_runbooks_no_longer_claim_vault_resolves_the_backup_key() -> None:
    """Era la mentira más cara: `EnvSecretsProvider` lee `os.environ`, y las
    unseal keys NO descifran AES-GCM. Creerlo convierte el primer DR real en una
    pérdida total."""
    body = _prose("04-disaster-recovery.md")
    assert "Vault NO resuelve la clave del backup" in body
    assert "unseal keys NO descifran" in body
    manual = _prose("dr-manual-backup.md")
    assert "La clave NO se resuelve de Vault" in manual


def test_the_entry_runbook_declares_rpo_and_rto_with_numbers() -> None:
    body = _prose("04-disaster-recovery.md")
    assert "RPO" in body and "RTO" in body
    assert "≤ 24 h" in body, "el RPO tiene que llevar una cifra, no una intención"
    assert "≤ 4 h" in body, "el RTO tiene que llevar una cifra"


def test_the_runbooks_describe_the_fail_stopped_restore() -> None:
    """El código obedece al procedimiento desde prod-04; el procedimiento tiene
    que seguir describiéndolo o volverán a divergir."""
    body = _prose("04-disaster-recovery.md")
    assert "PARADO" in body
    assert "RestorePartialError" in body
    assert "./scripts/restore.sh" in _prose("dr-full-restore.md")


def test_the_drill_runbook_forbids_consulting_the_origin_machine() -> None:
    """Sin esa regla el simulacro no prueba nada: siempre habrá alguien que
    'solo mira' el .env original y el drill sale bien por el motivo equivocado."""
    body = _prose("dr-drill.md")
    assert "nadie consulta la máquina origen" in body
    assert "ACTA DE SIMULACRO" in body
    assert "RTO REAL" in body


@pytest.mark.parametrize("name", _DR_RUNBOOKS)
def test_every_dr_runbook_has_frontmatter(name: str) -> None:
    body = _read(name)
    assert body.startswith("---\n"), f"{name} no tiene frontmatter YAML"
    front = yaml.safe_load(body.split("---", 2)[1])
    assert front.get("docs_language") == "es"
    assert front.get("title")
