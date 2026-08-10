"""La documentación operativa de prod-10 dice lo que el código hace.

Plan prod-10 `task_prod10_12`.

## Qué clase de test es este, y qué NO es

No comprueba prosa: comprueba **anclas**. Cada aserción de abajo corresponde a
una pieza que las fases A-C de este plan entregaron y que un operador sólo va a
encontrar si el runbook la nombra. El modo de fallo que previene es el §5 de
`verificar-antes-de-implementar.md` —«mecanismo entregado, cero llamantes»— en su
variante documental: un script que existe, funciona, y que nadie ejecutará porque
el procedimiento sigue describiendo el mundo anterior.

Es exactamente lo que había pasado ya dos veces en este repo: el runbook de
rotación dirigía la **revocación de emergencia** a un job que era un no-op
(gap2-1), y `05-key-rotation.md` describía una «verificación sin reinicio» que el
código no podía cumplir.

Deliberadamente laxo con la redacción y estricto con los nombres: reescribir un
párrafo no puede poner esto en rojo; borrar la única mención a
`vault-mint-service-tokens.sh`, sí.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOKS = _REPO_ROOT / "docs" / "06-runbooks"
_REFERENCE = _REPO_ROOT / "docs" / "04-reference"


def _read(path: Path) -> str:
    assert path.is_file(), f"falta el documento {path.relative_to(_REPO_ROOT).as_posix()}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# restart-services.md — el desellado es el PRIMER paso post-reinicio
# ---------------------------------------------------------------------------
def test_restart_services_opens_with_the_unseal_step() -> None:
    """Tras un reinicio del host, todo el stack arranca contra un Vault sellado
    que el healthcheck reporta sano (`sealedcode=200`, a propósito). Si el paso
    de desellado no es lo PRIMERO del runbook, el operador reinicia servicios sin
    entender por qué nada resuelve un secreto."""
    text = _read(_RUNBOOKS / "restart-services.md")
    head = (
        text[: text.index("## Comprobación previa")] if "## Comprobación previa" in text else text
    )

    assert (
        "unseal" in head.lower() or "desellar" in head.lower()
    ), "restart-services.md no menciona el desellado antes de sus pasos normales"
    assert "operator unseal" in head, "no dice el comando concreto para desellar"
    assert "dr-vault-unseal-rotation.md" in text, "no enlaza el runbook de custodias"


def test_restart_services_names_the_signal_the_operator_will_see() -> None:
    """Un runbook que dice «desella» sin decir «así sabrás que hace falta» se lee
    después del incidente, no durante."""
    text = _read(_RUNBOOKS / "restart-services.md")
    assert "agentic_vault_sealed" in text, "no nombra la métrica que delata el sellado"


# ---------------------------------------------------------------------------
# 05-key-rotation.md — token de servicio y propagación
# ---------------------------------------------------------------------------
def test_key_rotation_documents_the_vault_service_token() -> None:
    text = _read(_RUNBOOKS / "05-key-rotation.md")
    assert "vault-mint-service-tokens.sh" in text, (
        "el runbook no nombra el script que acuña los tokens por servicio: sin "
        "eso, el operador seguirá poniendo el root token en las configs"
    )
    for var in ("API_SERVER_VAULT_TOKEN", "WORKERS_VAULT_TOKEN"):
        assert var in text, f"{var} no aparece en el runbook de rotación"


def test_key_rotation_documents_the_propagation_script() -> None:
    """`task_prod05_06`: la propagación dejó de ser ocho pasos a mano."""
    text = _read(_RUNBOOKS / "05-key-rotation.md")
    assert "rotate-platform-secret.sh" in text


def test_key_rotation_keeps_the_minio_order_explicit() -> None:
    """El invariante caro del patrón add-then-remove. Si el runbook deja de decir
    que la revocación va DESPUÉS de propagar, alguien lo hará al revés y dejará a
    la plataforma sin object storage (riesgo 4 del plan prod-05)."""
    text = _read(_RUNBOOKS / "05-key-rotation.md")
    assert "revoke_previous_minio_credential" in text or "revoke-previous-minio" in text
    lowered = text.lower()
    assert (
        "solo entonces" in lowered or "sólo después" in lowered
    ), "el runbook ya no marca que la revocación de MinIO va DESPUÉS del reinicio"


# ---------------------------------------------------------------------------
# 04-reference — el catálogo de variables obligatorias
# ---------------------------------------------------------------------------
def test_the_reference_lists_every_variable_the_canonical_compose_demands() -> None:
    """Guarda de descubrimiento contra el compose, no una lista a mano: toda
    variable que el compose canónico exige con `${VAR:?…}` tiene que estar en el
    catálogo, o el arranque fallará con un nombre que no aparece en la
    documentación."""
    import re

    compose = (_REPO_ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", compose))
    assert len(required) >= 5, f"la guarda dejó de encontrar variables exigidas (vio {required})"

    catalogue = _read(_REFERENCE / "mandatory-env-vars.md")
    missing = sorted(var for var in required if var not in catalogue)
    assert not missing, (
        "el compose canónico exige estas variables y el catálogo no las documenta: " f"{missing}"
    )


def test_the_reference_explains_the_fail_closed_startup_error() -> None:
    text = _read(_REFERENCE / "mandatory-env-vars.md")
    assert "API_SERVER_ENVIRONMENT" in text


def test_troubleshooting_explains_the_fail_closed_startup_error() -> None:
    """El error de arranque fail-closed es, por diseño, la primera cosa que ve
    quien despliega mal. Sin entrada en troubleshooting parece una avería."""
    text = _read(_RUNBOOKS / "02-troubleshooting.md")
    assert "API_SERVER_ENVIRONMENT" in text
    assert "NOAUTH" in text, "Redis con requirepass es un síntoma nuevo sin entrada"


# ---------------------------------------------------------------------------
# dr-vault-unseal-rotation.md — el procedimiento que espera a un humano
# ---------------------------------------------------------------------------
def test_the_dr_runbook_carries_the_open_incident_procedure() -> None:
    """`task_prod10_01` NO la puede cerrar un agente: exige custodias y el umbral
    de Shamir. Lo que sí se puede entregar —y se comprueba aquí— es que el
    procedimiento esté escrito para que la persona no improvise."""
    text = _read(_RUNBOOKS / "dr-vault-unseal-rotation.md")

    for anchor in (
        "generate-root",  # cómo re-emitir el root token
        "token revoke",  # cómo revocar el expuesto
        "vault-mint-service-tokens.sh",  # el paso 2, que protege al 3
        "check_no_secret_artifacts.py",  # cómo comprobar que quedó cerrado
    ):
        assert anchor in text, f"el procedimiento de custodia no menciona {anchor!r}"

    assert "sdelete" in text.lower() or "cipher /w" in text.lower(), (
        "no dice cómo borrar de forma segura en Windows, que es el host del "
        "operador; `shred` no existe ahí"
    )
