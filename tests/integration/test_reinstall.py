"""Reinstall over an existing deployment — reinstall.sh / CLI (task_15_13).

Exercises the reinstall orchestration (:mod:`installer_backend.reinstall` + the
CLI's ``reinstall`` subcommand) with EVERY host-touching action MOCKED behind
injectable seams — NO real detection of ``/data``, NO real ``docker compose
down``, NO real deletion, NO real ``.env`` read. The real bindings are exercised
only by the plan's Tests Humanos (``human_15_04``: "Reinstalación sobre datos
existentes").

Re-running the installer over a machine that may already hold a deployment must
decide what to do with the data already there. This suite pins the contract:

  * PRESERVE keeps the data AND reuses the existing secrets/Vault material (no
    data orphaning — regenerating secrets would orphan the kept encrypted data,
    so PRESERVE must reuse, never regenerate);
  * a FRESH reinstall wipes the data ONLY after the double confirmation (type the
    name + explicit yes); a single/failed confirmation wipes NOTHING;
  * detecting NO prior install behaves like a first install (fresh, no wipe, no
    confirmation — there is no data to destroy).
"""

from __future__ import annotations

import io
from dataclasses import fields
from pathlib import Path

import pytest
from installer_backend.cli import (
    CliError,
    ExitCode,
    FlagConfirmer,
    HeadlessInstaller,
    StubCredentialBuilder,
    StubPrereqChecker,
    _assert_real_reinstall_seams,
    load_install_config,
    main,
    run_reinstall,
)
from installer_backend.command_runner import CommandResult, FakeCommandRunner
from installer_backend.compose_generator import PROJECT_NAME
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import (
    GeneratedSecrets,
    generate_env_file,
    generate_secrets,
)
from installer_backend.finalize import FinalizeService
from installer_backend.install import (
    INSTALL_STEP_ORDER,
    FakeStepExecutor,
    InstallStep,
    StepExecutionError,
)
from installer_backend.real_teardown import (
    FakeFileSystem,
    RealDataPurger,
    RealStackTeardown,
)
from installer_backend.reinstall import (
    _DATA_BOUND_SECRETS,
    _MONITORING_SECRETS,
    _ROTATABLE_SECRETS,
    PRESERVE_STEP_ORDER,
    SECRETS_NOT_IN_THE_ENV,
    ExistingInstall,
    FakeEnvFileReader,
    MissingExistingSecretError,
    RealExistingSecretLoader,
    RealInstallDetector,
    ReinstallAbortedError,
    Reinstaller,
    ReinstallMode,
    ReinstallRequest,
    StubExistingSecretLoader,
    StubInstallDetector,
    build_default_reinstaller,
    parse_env_text,
    run_preserve_pipeline,
    secrets_from_env,
)
from installer_backend.seams import StubInstallerLifecycle
from installer_backend.uninstall import (
    ScriptedConfirmer,
    StubDataPurger,
    StubStackTeardown,
)

pytestmark = pytest.mark.integration

_DEPLOYMENT = PROJECT_NAME
_DATA_ROOT = "/data/agent-platform"

#: Una configuración de instalación válida y mínima. Se construye con el
#: cargador REAL (`load_install_config`) para que estos tests se rompan si el
#: esquema del `install.yaml` cambia, en vez de fabricar un objeto a mano que
#: seguiría pareciendo válido cuando ya no lo es.
_VALID_YAML = f"""\
system:
  domain: agentic.example.com
  environment: production
resources:
  worker_replicas: 2
  worker_memory_gib: 4
  gpu_enabled: false
  embedding_model: nomic-embed-text
storage:
  data_root: {_DATA_ROOT}
  minio_bucket: agentic-platform
  minio_access_key: throwaway-access
  minio_secret_key: throwaway-secret-value-123
providers:
  ollama:
    enabled: true
    endpoint: http://o:11434
tenant:
  tenant_name: Acme Corp
  admin_email: admin@acme.com
"""


def _config() -> InstallerConfig:
    return load_install_config(_VALID_YAML)


def _config_file(tmp_path: Path) -> str:
    path = tmp_path / "install.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return str(path)


def _reinstaller(
    *,
    data_dir_present: bool,
    stack_running: bool,
    confirmer: ScriptedConfirmer,
    secret_available: bool = True,
) -> tuple[
    Reinstaller,
    StubInstallDetector,
    StubExistingSecretLoader,
    StubStackTeardown,
    StubDataPurger,
    io.StringIO,
]:
    """Build a Reinstaller wired to recording fakes; return it + the seams + stdout."""

    detector = StubInstallDetector(data_dir_present=data_dir_present, stack_running=stack_running)
    loader = StubExistingSecretLoader(available=secret_available)
    teardown = StubStackTeardown()
    purger = StubDataPurger()
    out = io.StringIO()
    inst = Reinstaller(
        detector=detector,
        secret_loader=loader,
        teardown=teardown,
        purger=purger,
        confirmer=confirmer,
        out=out,
    )
    return inst, detector, loader, teardown, purger, out


def _request(*, preserve: bool) -> ReinstallRequest:
    return ReinstallRequest(
        preserve=preserve,
        deployment_name=_DEPLOYMENT,
        data_root=_DATA_ROOT,
    )


# ---------------------------------------------------------------------------
# PRESERVE -> data kept + existing secrets/Vault reused (no orphaning).
# ---------------------------------------------------------------------------
def test_preserve_keeps_data_and_reuses_existing_secrets() -> None:
    # Existing install present; operator preserves (no confirmation needed).
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, detector, loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    result = inst.run(_request(preserve=True))

    # The detector probed the right deployment.
    assert detector.probed == (_DATA_ROOT, _DEPLOYMENT)
    # PRESERVE mode: data kept, existing secrets REUSED (no regeneration).
    assert result.mode is ReinstallMode.PRESERVE
    assert result.data_preserved is True
    assert result.reused_existing_secrets is True
    # The existing material was actually loaded + carried for the install to reuse.
    assert loader.loaded is True
    assert result.existing_secrets is not None
    assert "POSTGRES_PASSWORD" in result.existing_secrets.env_values
    # Aquí se afirmaba además `result.existing_secrets.vault_unseal_keys`. Ese
    # campo ya no existe: nada lo rellenaba y nada lo consumía, porque la
    # reinstalación PRESERVANDO no toca Vault — el ADR 0145 mantiene el
    # desellado MANUAL, con los fragmentos de Shamir en manos de personas, y un
    # instalador que los leyera de la misma máquina desmontaría esa decisión
    # disfrazado de comodidad. Ver el docstring del módulo.
    assert not hasattr(result.existing_secrets, "vault_unseal_keys")
    # The stack was stopped to apply the regenerated config, but WITHOUT removing
    # volumes — and the data purge seam was NEVER called (no orphaning, no wipe).
    assert teardown.torn_down is True
    assert teardown.removed_volumes is False
    assert purger.purged is False
    # Phase order: detect -> preserve -> teardown (no wipe).
    assert inst.phases == ["detect", "preserve", "teardown"]


def test_preserve_refuses_when_existing_secrets_unavailable() -> None:
    # Existing install present, but the old secrets can't be loaded -> a preserve
    # that minted new secrets would ORPHAN the encrypted data, so it must REFUSE
    # rather than silently regenerate.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True,
        stack_running=False,
        confirmer=confirmer,
        secret_available=False,
    )

    with pytest.raises(ReinstallAbortedError):
        inst.run(_request(preserve=True))

    # It tried to load the existing secrets, found none, and refused — nothing
    # destructive ran and the stack was NOT torn down.
    assert loader.loaded is True
    assert teardown.torn_down is False
    assert purger.purged is False


# ---------------------------------------------------------------------------
# FRESH -> wipes data ONLY after the double confirmation.
# ---------------------------------------------------------------------------
def test_fresh_without_confirmation_wipes_nothing() -> None:
    # Existing install present; FRESH requested but NO confirmation -> abort.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, _loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(ReinstallAbortedError):
        inst.run(_request(preserve=False))

    # NOTHING destructive ran: stack untouched, data intact.
    assert teardown.torn_down is False
    assert purger.purged is False
    # Only detect + the (failed) fresh-confirm phase ran; no teardown/wipe.
    assert inst.phases == ["detect", "confirm_fresh"]


def test_fresh_with_only_name_is_still_blocked() -> None:
    # Correct name typed, but NO explicit yes -> the double confirm fails.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[False])
    inst, _detector, _loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(ReinstallAbortedError):
        inst.run(_request(preserve=False))

    assert teardown.torn_down is False
    assert purger.purged is False


def test_fresh_wrong_name_is_blocked() -> None:
    # Explicit yes but a different stack name -> the first confirm fails.
    confirmer = ScriptedConfirmer(name_answer="some-other-stack", yes_answers=[True])
    inst, _detector, _loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(ReinstallAbortedError):
        inst.run(_request(preserve=False))

    assert teardown.torn_down is False
    assert purger.purged is False


def test_fresh_with_both_confirmations_wipes_and_regenerates() -> None:
    # Name typed + explicit yes -> the data is wiped + secrets regenerated.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[True])
    inst, _detector, loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    result = inst.run(_request(preserve=False))

    assert result.mode is ReinstallMode.FRESH
    assert result.data_preserved is False
    # FRESH regenerates everything: the existing secrets are NOT reused/loaded.
    assert result.reused_existing_secrets is False
    assert result.existing_secrets is None
    assert loader.loaded is False
    # Stack removed (with its volumes) AND the data root wiped.
    assert teardown.torn_down is True
    assert teardown.removed_volumes is True
    assert purger.purged is True
    assert purger.data_root == _DATA_ROOT
    # Phase order: detect -> confirm_fresh -> teardown -> wipe_data.
    assert inst.phases == ["detect", "confirm_fresh", "teardown", "wipe_data"]


# ---------------------------------------------------------------------------
# No prior install -> behaves like a first install.
# ---------------------------------------------------------------------------
def test_no_prior_install_behaves_like_first_install() -> None:
    # Detector finds nothing; preserve flag is moot.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, loader, teardown, purger, _out = _reinstaller(
        data_dir_present=False, stack_running=False, confirmer=confirmer
    )

    result = inst.run(_request(preserve=True))

    assert result.mode is ReinstallMode.FIRST_INSTALL
    assert result.reused_existing_secrets is False
    assert result.existing_secrets is None
    # Nothing to preserve OR wipe — no secret load, no teardown, no purge.
    assert loader.loaded is False
    assert teardown.torn_down is False
    assert purger.purged is False
    assert inst.phases == ["detect", "first_install"]


def test_no_prior_install_even_when_fresh_requested() -> None:
    # --fresh over an empty machine: still just a first install, no wipe, no
    # confirmation gate (there is no data to destroy).
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, _loader, teardown, purger, _out = _reinstaller(
        data_dir_present=False, stack_running=False, confirmer=confirmer
    )

    result = inst.run(_request(preserve=False))

    assert result.mode is ReinstallMode.FIRST_INSTALL
    assert teardown.torn_down is False
    assert purger.purged is False
    assert "confirm_fresh" not in inst.phases


def test_stack_running_only_counts_as_present() -> None:
    # Data dir gone but the stack is still up -> still a prior install (present).
    existing = ExistingInstall(data_dir_present=False, stack_running=True)
    assert existing.present is True
    none = ExistingInstall(data_dir_present=False, stack_running=False)
    assert none.present is False


# ---------------------------------------------------------------------------
# The CLI surface — exit codes via run_reinstall() + main().
# ---------------------------------------------------------------------------
def test_run_reinstall_fresh_without_yes_aborts(tmp_path: Path) -> None:
    # FRESH with the right name but no --yes -> ABORTED, nothing removed.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[False])
    inst, _detector, _loader, teardown, _purger, out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(Exception) as exc:
        run_reinstall(
            _config_file(tmp_path),
            deployment_name=_DEPLOYMENT,
            fresh=True,
            confirm_name=_DEPLOYMENT,
            yes=False,
            reinstaller=inst,
            out=out,
            dry_run=True,
        )
    assert getattr(exc.value, "code", None) == ExitCode.ABORTED
    assert teardown.torn_down is False


def test_main_reinstall_without_a_config_is_a_usage_error() -> None:
    # Este test AFIRMABA lo contrario: que `main(["reinstall"])` salía 0 diciendo
    # «instalación desde cero». Salía 0, sí, pero porque los cuatro seams eran
    # stubs — el detector respondía SIEMPRE «no hay instalación previa» aunque el
    # stack estuviese corriendo, y detrás no se ejecutaba ningún pipeline. El
    # test fijaba el defecto: daba por bueno un subcomando que no hacía nada.
    #
    # Ahora una reinstalación NECESITA la configuración con la que reinstalar
    # (regenera compose + config), así que sin --config es un error de uso y no
    # un éxito silencioso.
    out = io.StringIO()
    code = main(["reinstall"], out=out)
    assert code == int(ExitCode.USAGE)
    assert "instalación desde cero" not in out.getvalue().lower()


def test_run_reinstall_fresh_wrong_name_via_flagconfirmer_aborts(tmp_path: Path) -> None:
    # The flag-derived path (FlagConfirmer) aborts a FRESH wipe when the typed
    # --confirm-name does not match the deployment, even with --yes set.
    confirmer = FlagConfirmer(confirm_name_value="wrong-name", yes=True)
    inst, _detector, _loader, teardown, purger, out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(Exception) as exc:
        run_reinstall(
            _config_file(tmp_path),
            deployment_name=_DEPLOYMENT,
            fresh=True,
            confirm_name="wrong-name",
            yes=True,
            reinstaller=inst,
            out=out,
            dry_run=True,
        )
    assert getattr(exc.value, "code", None) == ExitCode.ABORTED
    assert teardown.torn_down is False
    assert purger.purged is False


def test_run_reinstall_fresh_with_both_confirmations_wipes(tmp_path: Path) -> None:
    # FlagConfirmer with the matching name + --yes covers the double confirm.
    confirmer = FlagConfirmer(confirm_name_value=_DEPLOYMENT, yes=True)
    inst, _detector, _loader, teardown, purger, out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    code = run_reinstall(
        _config_file(tmp_path),
        deployment_name=_DEPLOYMENT,
        fresh=True,
        confirm_name=_DEPLOYMENT,
        yes=True,
        reinstaller=inst,
        installer=_recording_installer(out),
        out=out,
        dry_run=True,
    )
    assert code == ExitCode.OK
    assert teardown.torn_down is True
    assert purger.purged is True


# ===========================================================================
# Los seams REALES (auditoría 2026-08-27, bloqueante).
#
# `build_default_reinstaller` cableaba los CUATRO stubs, así que `reinstall`
# salía 0 sin mirar el host, sin parar nada, sin cargar los secretos existentes
# y —lo peor— sin ejecutar después ningún pipeline: el operador seguía el
# runbook de upgrade, leía «No se detectó instalación previa», y daba el
# upgrade por hecho. El contraste con `uninstall`, que sí cableaba los bindings
# reales, es lo que demuestra que era un olvido y no un diseño.
# ===========================================================================
def _detector(
    *,
    data_root_exists: bool = False,
    ps_output: tuple[str, ...] = (),
    ps_returncode: int = 0,
) -> RealInstallDetector:
    """Un detector real con el runner y el FS falseados (ni Docker ni disco)."""

    runner = FakeCommandRunner(
        responses={
            ("docker", "compose", "-p", _DEPLOYMENT, "ps", "-q"): CommandResult(
                returncode=ps_returncode, output_lines=ps_output
            )
        }
    )
    fs = FakeFileSystem(existing={_DATA_ROOT} if data_root_exists else set())
    return RealInstallDetector(runner=runner, fs=fs)


def test_real_detector_sees_a_running_stack_through_compose_ps() -> None:
    existing = _detector(ps_output=("c0ffee1234",)).detect(
        data_root=_DATA_ROOT, project_name=_DEPLOYMENT
    )
    assert existing.stack_running is True
    assert existing.present is True


def test_real_detector_sees_the_data_root_on_disk() -> None:
    existing = _detector(data_root_exists=True).detect(
        data_root=_DATA_ROOT, project_name=_DEPLOYMENT
    )
    assert existing.data_dir_present is True
    assert existing.present is True


def test_real_detector_reports_nothing_on_a_clean_machine() -> None:
    existing = _detector().detect(data_root=_DATA_ROOT, project_name=_DEPLOYMENT)
    assert existing.present is False


def test_real_detector_refuses_to_guess_when_the_docker_probe_fails() -> None:
    # Si `docker compose ps` no responde no sabemos en qué estado está la
    # máquina, y la decisión que cuelga de aquí (preservar / borrar / instalar
    # desde cero) no admite una suposición: concluir «no hay nada» con Docker
    # roto llevaría a instalar desde cero, con secretos NUEVOS, encima de un
    # PGDATA existente. Se para.
    with pytest.raises(ReinstallAbortedError) as exc:
        _detector(ps_returncode=1).detect(data_root=_DATA_ROOT, project_name=_DEPLOYMENT)
    assert "docker" in str(exc.value).lower()


def test_real_secret_loader_returns_none_without_an_env_file() -> None:
    loader = RealExistingSecretLoader(reader=FakeEnvFileReader(files={}))
    assert loader.load(data_root=_DATA_ROOT) is None


def test_real_secret_loader_parses_the_existing_env() -> None:
    reader = FakeEnvFileReader(
        files={
            f"{_DATA_ROOT}/.env": (
                "# cabecera generada\n"
                "\n"
                "POSTGRES_PASSWORD=pg-existente\n"
                'SYSTEM_DOMAIN="dominio con espacios"\n'
                "API_SERVER_JWT_SECRET=jwt=con=iguales\n"
            )
        }
    )
    existing = RealExistingSecretLoader(reader=reader).load(data_root=_DATA_ROOT)
    assert existing is not None
    assert existing.env_values["POSTGRES_PASSWORD"] == "pg-existente"
    # Las comillas del generador se deshacen (round-trip de _quote_env_value).
    assert existing.env_values["SYSTEM_DOMAIN"] == "dominio con espacios"
    # Un '=' dentro del valor no parte la línea.
    assert existing.env_values["API_SERVER_JWT_SECRET"] == "jwt=con=iguales"


@pytest.mark.parametrize("monitoring", [False, True])
def test_reused_secrets_round_trip_through_a_generated_env_file(monitoring: bool) -> None:
    # EL test de la promesa de PRESERVE. Si esta reconstrucción pierde o cambia
    # un solo valor, el .env regenerado deja HUÉRFANOS los datos que la
    # reinstalación juraba preservar: Postgres rechaza la contraseña, MinIO no
    # abre el bucket y las columnas Fernet no descifran.
    #
    # Se comprueba contra el generador REAL —generar, parsear, reconstruir— y no
    # contra una lista de valores escrita a mano, que seguiría en verde el día
    # que alguien renombre una variable. Las dos superposiciones, porque la de
    # monitorización añade una variable que la base no tiene.
    original = generate_secrets()
    env_text = generate_env_file(_config(), original, monitoring=monitoring)

    parsed = parse_env_text(env_text)
    reused, regenerated = secrets_from_env(parsed, monitoring=monitoring)

    expected = {**_DATA_BOUND_SECRETS, **_ROTATABLE_SECRETS}
    if monitoring:
        expected |= _MONITORING_SECRETS
    for key, field_name in expected.items():
        assert key in parsed, f"{key} ya no está en el .env generado"
        assert getattr(reused, field_name) == getattr(original, field_name), field_name
    # Con un .env completo no se mintea nada: PRESERVAR es reutilizar, no rotar.
    assert regenerated == ()


def test_reused_secrets_refuse_when_a_data_bound_secret_is_missing() -> None:
    # Regenerar la clave Fernet de notificaciones no «rota» nada: deja las
    # columnas cifradas ilegibles para siempre. Preferimos parar.
    parsed = {"POSTGRES_PASSWORD": "x"}
    with pytest.raises(MissingExistingSecretError) as exc:
        secrets_from_env(parsed)
    assert "API_SERVER_NOTIFICATION_ENCRYPTION_KEY" in str(exc.value)


def test_reused_secrets_regenerate_only_the_rotatable_ones_and_name_them() -> None:
    # Un .env de una instalación anterior a que existiera un secreto (el caso
    # real: `API_SERVER_INTERNAL_TOKEN_SECRET` lo añadió el ADR 0136) no puede
    # bloquear la reinstalación — pero el operador tiene que ENTERARSE de que
    # ese secreto se ha rotado, porque las sesiones se caen.
    parsed = parse_env_text(generate_env_file(_config(), generate_secrets()))
    del parsed["API_SERVER_JWT_SECRET"]

    reused, regenerated = secrets_from_env(parsed)

    assert regenerated == ("API_SERVER_JWT_SECRET",)
    assert reused.jwt_secret  # se ha minteado uno nuevo, no queda vacío


def test_every_generated_secret_is_classified_for_reuse() -> None:
    # La guarda que impide que esto envejezca EN SILENCIO. Un secreto nuevo en
    # `GeneratedSecrets` que nadie clasifique aquí no da error: la reinstalación
    # PRESERVANDO le pondría un valor nuevo y dejaría huérfano lo que estuviera
    # atado a él. Ya ha pasado una vez —`service_user_password` apareció
    # mientras se escribía esto—, así que la lista se comprueba contra el
    # dataclass real, no contra la memoria de quien lo tocó por última vez.
    classified = (
        set(_DATA_BOUND_SECRETS.values())
        | set(_ROTATABLE_SECRETS.values())
        | set(_MONITORING_SECRETS.values())
        | set(SECRETS_NOT_IN_THE_ENV)
    )
    declared = {f.name for f in fields(GeneratedSecrets)}
    assert declared == classified, (
        "hay secretos sin clasificar para la reinstalación PRESERVANDO: "
        f"{sorted(declared - classified)}. Decide si cada uno está atado a datos "
        "en disco (_DATA_BOUND_SECRETS: su pérdida los deja ilegibles), si se "
        "puede rotar (_ROTATABLE_SECRETS), si sólo existe con la superposición "
        "de monitorización (_MONITORING_SECRETS) o si no llega nunca al .env "
        "(SECRETS_NOT_IN_THE_ENV)."
    )


def _recording_installer(out: io.StringIO) -> HeadlessInstaller:
    """Un HeadlessInstaller con seams de simulación que RECUERDA los pasos.

    Sirve para afirmar que la reinstalación encadena de verdad el pipeline de
    instalación —lo que no hacía— sin levantar Docker.
    """

    return HeadlessInstaller(
        prereq_checker=StubPrereqChecker(),
        executor=FakeStepExecutor(),
        credential_builder=StubCredentialBuilder(),
        finalize=FinalizeService(lifecycle=StubInstallerLifecycle()),
        out=out,
    )


# ===========================================================================
# El pipeline encadenado: `reinstaller.run()` sólo hacía el trabajo PREVIO y
# nadie ejecutaba nada después. Un `reinstall` que detecta, para el stack y
# vuelve con exit 0 sin reinstalar es peor que uno que falla: el operador se
# va convencido de haber actualizado.
# ===========================================================================
def test_preserve_pipeline_runs_only_the_four_regeneration_steps() -> None:
    executor = FakeStepExecutor()
    out = io.StringIO()

    run_preserve_pipeline(executor, config={}, out=out)

    assert executor.executed == list(PRESERVE_STEP_ORDER)
    # Los dos pasos que NO corren, y el porqué está en el docstring de
    # `run_preserve_pipeline`: re-inicializar Vault sobre uno ya inicializado no
    # tiene vuelta atrás, y re-sembrar el tenant mintearía una contraseña de
    # administrador nueva para una cuenta que ya existe.
    assert InstallStep.BOOTSTRAP_VAULT not in executor.executed
    assert InstallStep.SEED_TENANT not in executor.executed


def test_preserve_pipeline_tells_the_operator_to_unseal_vault_by_hand() -> None:
    # Vault se sella en cuanto para su contenedor. Como el pipeline no lo
    # desella (ADR 0145: desellado manual), callárselo dejaría al operador con
    # media plataforma sin secretos y sin saber por qué.
    out = io.StringIO()
    run_preserve_pipeline(FakeStepExecutor(), config={}, out=out)
    text = out.getvalue().lower()
    assert "vault" in text and "sellado" in text


def test_preserve_pipeline_halts_on_a_failed_step() -> None:
    # Un paso que falla no puede seguir hacia los siguientes ni acabar en verde.
    executor = FakeStepExecutor(fail_at=InstallStep.START_STACK)
    with pytest.raises(StepExecutionError):
        run_preserve_pipeline(executor, config={}, out=io.StringIO())
    assert InstallStep.RUN_MIGRATIONS not in executor.executed


def test_run_reinstall_first_install_chains_the_full_install_pipeline(tmp_path: Path) -> None:
    # Sin instalación previa, reinstalar ES instalar: los seis pasos y el
    # revelado de credenciales.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, _loader, _teardown, _purger, out = _reinstaller(
        data_dir_present=False, stack_running=False, confirmer=confirmer
    )
    installer = _recording_installer(out)

    code = run_reinstall(
        _config_file(tmp_path),
        deployment_name=_DEPLOYMENT,
        fresh=False,
        confirm_name="",
        yes=False,
        reinstaller=inst,
        installer=installer,
        out=out,
        dry_run=True,
    )

    assert code == ExitCode.OK
    assert installer.executor.executed == list(INSTALL_STEP_ORDER)


def test_run_reinstall_fresh_chains_the_full_pipeline_after_the_wipe(tmp_path: Path) -> None:
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[True])
    inst, _detector, _loader, teardown, purger, out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )
    installer = _recording_installer(out)

    code = run_reinstall(
        _config_file(tmp_path),
        deployment_name=_DEPLOYMENT,
        fresh=True,
        confirm_name=_DEPLOYMENT,
        yes=True,
        reinstaller=inst,
        installer=installer,
        out=out,
        dry_run=True,
    )

    assert code == ExitCode.OK
    # Se borró primero y se instaló después, en ese orden.
    assert purger.purged is True
    assert teardown.removed_volumes is True
    assert installer.executor.executed == list(INSTALL_STEP_ORDER)


def test_run_reinstall_preserve_regenerates_with_the_reused_secrets(tmp_path: Path) -> None:
    # El camino que documentan los dos runbooks para un upgrade. Tiene que
    # ejecutar la regeneración Y hacerlo con los secretos que ya había.
    env_values = parse_env_text(generate_env_file(_config(), generate_secrets()))
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    detector = StubInstallDetector(data_dir_present=True, stack_running=True)
    loader = StubExistingSecretLoader(env_values=env_values)
    teardown = StubStackTeardown()
    out = io.StringIO()
    inst = Reinstaller(
        detector=detector,
        secret_loader=loader,
        teardown=teardown,
        purger=StubDataPurger(),
        confirmer=confirmer,
        out=out,
    )
    executor = FakeStepExecutor()

    code = run_reinstall(
        _config_file(tmp_path),
        deployment_name=_DEPLOYMENT,
        fresh=False,
        confirm_name="",
        yes=False,
        reinstaller=inst,
        preserve_executor=executor,
        out=out,
        dry_run=True,
    )

    assert code == ExitCode.OK
    assert executor.executed == list(PRESERVE_STEP_ORDER)
    # El stack se paró SIN llevarse los volúmenes: los datos siguen ahí.
    assert teardown.removed_volumes is False


def test_run_reinstall_preserve_refuses_when_a_data_bound_secret_is_missing(
    tmp_path: Path,
) -> None:
    # Un .env existente al que le falta una clave Fernet: reinstalar encima
    # mintearía una nueva y dejaría las columnas cifradas ilegibles. Se aborta
    # ANTES de regenerar nada.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    detector = StubInstallDetector(data_dir_present=True, stack_running=True)
    loader = StubExistingSecretLoader(env_values={"POSTGRES_PASSWORD": "solo-esta"})
    executor = FakeStepExecutor()
    out = io.StringIO()
    inst = Reinstaller(
        detector=detector,
        secret_loader=loader,
        teardown=StubStackTeardown(),
        purger=StubDataPurger(),
        confirmer=confirmer,
        out=out,
    )

    with pytest.raises(Exception) as exc:
        run_reinstall(
            _config_file(tmp_path),
            deployment_name=_DEPLOYMENT,
            fresh=False,
            confirm_name="",
            yes=False,
            reinstaller=inst,
            preserve_executor=executor,
            out=out,
            dry_run=True,
        )

    assert getattr(exc.value, "code", None) == ExitCode.ABORTED
    assert executor.executed == [], "no se regeneró nada tras la negativa"


# ---------------------------------------------------------------------------
# La guarda anti-simulación, con la MISMA forma que la de install/uninstall.
# ---------------------------------------------------------------------------
def test_default_reinstaller_wires_the_real_seams() -> None:
    inst = build_default_reinstaller(io.StringIO(), ScriptedConfirmer(), data_root=_DATA_ROOT)
    assert isinstance(inst.detector, RealInstallDetector)
    assert isinstance(inst.secret_loader, RealExistingSecretLoader)
    assert isinstance(inst.teardown, RealStackTeardown)
    assert isinstance(inst.purger, RealDataPurger)
    _assert_real_reinstall_seams(inst, dry_run=False)  # no levanta


def test_guard_rejects_the_stub_seams_without_dry_run() -> None:
    sim = build_default_reinstaller(
        io.StringIO(), ScriptedConfirmer(), data_root=_DATA_ROOT, dry_run=True
    )
    with pytest.raises(CliError) as exc:
        _assert_real_reinstall_seams(sim, dry_run=False)
    assert exc.value.code is ExitCode.PROVISION
    assert "--dry-run" in str(exc.value)
    # Los cuatro seams de simulación se nombran, no sólo el primero.
    message = str(exc.value)
    for stub in ("StubInstallDetector", "StubExistingSecretLoader", "StubStackTeardown"):
        assert stub in message


def test_guard_is_a_noop_under_dry_run() -> None:
    sim = build_default_reinstaller(
        io.StringIO(), ScriptedConfirmer(), data_root=_DATA_ROOT, dry_run=True
    )
    _assert_real_reinstall_seams(sim, dry_run=True)  # no levanta


def test_dry_run_preserve_never_builds_a_real_executor(tmp_path: Path) -> None:
    # El agujero que dejaba la primera versión del encadenado: con --dry-run y
    # sin ejecutor inyectado, el camino PRESERVAR construía el ejecutor REAL y
    # regeneraba de verdad. Hoy no se llega ahí con los stubs por defecto (el
    # detector dice «no hay instalación previa»), pero «hoy no se llega» no es
    # una garantía: es una coincidencia del cableado.
    env_values = parse_env_text(generate_env_file(_config(), generate_secrets()))
    out = io.StringIO()
    inst = Reinstaller(
        detector=StubInstallDetector(data_dir_present=True, stack_running=True),
        secret_loader=StubExistingSecretLoader(env_values=env_values),
        teardown=StubStackTeardown(),
        purger=StubDataPurger(),
        confirmer=ScriptedConfirmer(),
        out=out,
    )

    code = run_reinstall(
        _config_file(tmp_path),
        deployment_name=_DEPLOYMENT,
        fresh=False,
        confirm_name="",
        yes=False,
        reinstaller=inst,
        out=out,
        dry_run=True,
    )

    assert code == ExitCode.OK
    assert "SIMULACIÓN" in out.getvalue()
