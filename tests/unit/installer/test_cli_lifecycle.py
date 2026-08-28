"""El ciclo de vida de una instalación, visto desde el CLI.

Tres propiedades que no viven en ningún módulo suelto porque son de CABLEADO, y
el cableado es justo donde se perdieron:

1. **Relanzar el instalador no acuña secretos nuevos sobre datos viejos.** La
   lógica está en :mod:`installer_backend.install_state`; lo que se afirma aquí
   es que los constructores del CLI la CONSULTAN — hasta el 2026-08-27 llamaban
   a ``generate_secrets()`` incondicionalmente, en las dos líneas
   (``build_default_installer`` y ``build_default_generator``), y nadie miraba si
   ya había un ``.env``.
2. **Las unseal keys tienen red antes de imprimirse.** El depósito vive en
   :mod:`installer_backend.key_escrow`; aquí se afirma que el ejecutor lo tiene
   para depositarlas y el instalador el MISMO para retirarlas tras el revelado.
   Cablear sólo uno de los dos es peor que no cablear ninguno: dejaría el
   fichero con las cinco claves en la máquina para siempre.
3. **Ninguna excepción sale como traza.** El instalador toca el sistema de
   ficheros de una máquina que no controla; la lista de cosas que pueden fallar
   no se puede enumerar por adelantado, así que la última red es genérica.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pytest
from installer_backend.cli import (
    CliError,
    ExitCode,
    HeadlessInstaller,
    RealCredentialBuilder,
    build_default_generator,
    build_default_installer,
    build_parser,
    main,
    run_generate,
    run_install,
)
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import (
    FakeEnvFileWriter,
    generate_env_file,
    generate_secrets,
)
from installer_backend.finalize import FinalizeService
from installer_backend.install import FakeStepExecutor
from installer_backend.install_state import (
    ENV_FILENAME,
    DataRootInspector,
    FakeFileReader,
    UnsafeOverwriteError,
)
from installer_backend.key_escrow import (
    UNSEAL_KEYS_FILENAME,
    FakeEscrowFile,
    FileKeyEscrow,
)
from installer_backend.real_step_executor import RealStepExecutor
from installer_backend.seams import StubInstallerLifecycle
from installer_backend.vault_bootstrap import VaultBootstrapResult, VaultInitResult

pytestmark = pytest.mark.unit

_ROOT = "/data/agent-platform"
_ENV = f"{_ROOT}/{ENV_FILENAME}"

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
  data_root: {_ROOT}
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


def _config_file(tmp_path) -> str:
    path = tmp_path / "install.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    return str(path)


def _inspector(files: dict[str, str]) -> DataRootInspector:
    """Un inspector sobre ficheros en memoria: el test no toca ``/data``."""

    return DataRootInspector(
        reader=FakeFileReader(files=dict(files)),
        writer=FakeEnvFileWriter(),
        now=lambda: "20260828T120000",
    )


@dataclass
class RefusingInspector:
    """Un inspector que siempre se niega (la raíz de datos no es reutilizable)."""

    message: str = "hay datos y no se pueden releer"

    def resolve_secrets(self, data_root: str, *, force_new: bool, monitoring: bool = False):
        raise UnsafeOverwriteError(self.message)


# ---------------------------------------------------------------------------
# 1. Relanzar reutiliza — en los DOS constructores
# ---------------------------------------------------------------------------
def test_the_default_installer_reuses_the_secrets_already_on_disk(
    installer_config: InstallerConfig,
) -> None:
    """`install` sobre una raíz de datos con `.env` no mintea nada nuevo."""

    first = generate_secrets()
    inst = build_default_installer(
        io.StringIO(),
        installer_config,
        inspector=_inspector({_ENV: generate_env_file(installer_config, first)}),
    )

    executor = inst.executor
    assert isinstance(executor, RealStepExecutor)
    assert executor.secrets.postgres_password == first.postgres_password
    assert executor.secrets.sso_encryption_key == first.sso_encryption_key


def test_the_default_generator_reuses_them_too(installer_config: InstallerConfig) -> None:
    """Y `generate` igual: es el camino publicado, y el que más se relanza.

    Los dos constructores llamaban a `generate_secrets()` por su cuenta, en dos
    líneas distintas. Arreglar uno solo habría dejado el defecto entero en el
    camino del ADR 0161, que es precisamente donde el operador va a reintentar
    (el contenedor no deja rastro, así que relanzarlo parece gratis).
    """

    first = generate_secrets()
    gen = build_default_generator(
        io.StringIO(),
        installer_config,
        inspector=_inspector({_ENV: generate_env_file(installer_config, first)}),
    )

    executor = gen.executor
    assert isinstance(executor, RealStepExecutor)
    assert executor.secrets.minio_root_password == first.minio_root_password


def test_a_clean_data_root_still_mints_fresh_secrets(
    installer_config: InstallerConfig,
) -> None:
    """Control positivo: sobre una máquina limpia se sigue acuñando de verdad.

    Sin él, un arreglo que devolviera siempre lo mismo pasaría los dos tests de
    arriba y dejaría dos instalaciones distintas con los mismos secretos.
    """

    a = build_default_generator(io.StringIO(), installer_config, inspector=_inspector({}))
    b = build_default_generator(io.StringIO(), installer_config, inspector=_inspector({}))

    assert isinstance(a.executor, RealStepExecutor)
    assert isinstance(b.executor, RealStepExecutor)
    assert a.executor.secrets.postgres_password != b.executor.secrets.postgres_password


def test_the_decision_is_printed_and_never_carries_a_secret(
    installer_config: InstallerConfig,
) -> None:
    """«Nunca en silencio»: la reutilización se anuncia, sin valores dentro."""

    first = generate_secrets()
    out = io.StringIO()
    build_default_generator(
        out,
        installer_config,
        inspector=_inspector({_ENV: generate_env_file(installer_config, first)}),
    )

    printed = out.getvalue()
    assert "Reutilizando" in printed
    assert first.postgres_password not in printed


@pytest.mark.parametrize("command", ["install", "generate"])
def test_a_refusal_to_overwrite_has_its_own_documented_exit_code(tmp_path, command: str) -> None:
    """Negarse a pisar una instalación NO es «argumentos mal» ni «falló un paso».

    Necesita código propio porque lo que tiene que hacer quien lo recoja es lo
    contrario de reintentar: reintentar el mismo comando falla igual para
    siempre. Hace falta que un humano recupere el `.env` o asuma la pérdida.
    """

    run = run_install if command == "install" else run_generate

    with pytest.raises(CliError) as excinfo:
        run(_config_file(tmp_path), out=io.StringIO(), inspector=RefusingInspector())

    assert excinfo.value.code is ExitCode.UNSAFE
    assert "no se pueden releer" in str(excinfo.value)


def test_every_exit_code_is_distinct() -> None:
    """Dos códigos con el mismo número harían inútil la tabla documentada.

    La automatización del operador ramifica por número; si dos clases de fallo
    comparten uno, el `case 7)` de su script hace lo contrario de lo que debe en
    la mitad de los casos.
    """

    values = [int(code) for code in ExitCode]
    assert len(values) == len(set(values)), sorted(values)


# ---------------------------------------------------------------------------
# 2. El depósito de unseal keys, cableado en los dos extremos
# ---------------------------------------------------------------------------
def test_the_installer_wires_the_same_escrow_to_both_ends(
    installer_config: InstallerConfig,
) -> None:
    """El ejecutor deposita y el instalador retira: tiene que ser el MISMO objeto.

    Con dos instancias distintas el depósito se escribiría y no se borraría
    nunca, dejando las cinco unseal keys y el root token en un fichero, en la
    misma máquina que Vault, para siempre. Sería cambiar una pérdida por una
    fuga.
    """

    inst = build_default_installer(io.StringIO(), installer_config, inspector=_inspector({}))

    executor = inst.executor
    assert isinstance(executor, RealStepExecutor)
    assert executor.key_escrow is not None
    assert inst.key_escrow is executor.key_escrow


def test_the_reveal_discards_the_escrow(installer_config: InstallerConfig) -> None:
    """En cuanto las claves están en pantalla, el fichero deja de tener sentido."""

    store = FakeEscrowFile()
    escrow = FileKeyEscrow(data_root=_ROOT, store=store)
    escrow.store_init(VaultInitResult(unseal_keys=("a", "b", "c"), root_token="t", key_threshold=2))

    inst = HeadlessInstaller(
        prereq_checker=_AlwaysOkPrereqs(),
        executor=FakeStepExecutor(),
        credential_builder=_ScriptedCredentials(),
        finalize=FinalizeService(lifecycle=StubInstallerLifecycle()),
        out=io.StringIO(),
        key_escrow=escrow,
    )

    inst.run(installer_config)

    assert store.files == {}, "el depósito ha sobrevivido al revelado"


def test_a_leftover_escrow_is_named_when_there_is_nothing_to_reveal(
    installer_config: InstallerConfig,
) -> None:
    """El reintento sobre un Vault ya inicializado tiene que apuntar al fichero.

    Sin esto el operador recibe «No hay credenciales reales que revelar» y no
    tiene forma de saber que sus cinco claves están en esa misma máquina, a un
    `cat` de distancia. Con esto, el error ES el procedimiento de recuperación.
    """

    store = FakeEscrowFile()
    escrow = FileKeyEscrow(data_root=_ROOT, store=store)
    escrow.store_init(VaultInitResult(unseal_keys=("a", "b", "c"), root_token="t", key_threshold=2))

    executor = _executor_with(
        installer_config,
        vault_result=VaultBootstrapResult(
            init=None,
            already_initialized=True,
            kv_mount="agentic",
            kv_enabled=False,
            policies_written=(),
        ),
    )

    with pytest.raises(CliError) as excinfo:
        RealCredentialBuilder(executor, escrow=escrow).build(installer_config)

    assert UNSEAL_KEYS_FILENAME in str(excinfo.value)
    assert excinfo.value.code is ExitCode.PROVISION


def test_the_reveal_prints_the_credential_builders_advisories(
    installer_config: InstallerConfig,
) -> None:
    """Si la contraseña revelada puede no abrir la cuenta, se dice AHÍ.

    El revelado es de una sola vez, sin recuperación, y va seguido de la
    autodestrucción del instalador: la duda tiene que viajar pegada al dato o se
    pierde con él.
    """

    out = io.StringIO()
    inst = HeadlessInstaller(
        prereq_checker=_AlwaysOkPrereqs(),
        executor=FakeStepExecutor(),
        credential_builder=_ScriptedCredentials(notes=("AVISO: el usuario ya existía",)),
        finalize=FinalizeService(lifecycle=StubInstallerLifecycle()),
        out=out,
    )

    inst.run(installer_config)

    assert "AVISO: el usuario ya existía" in out.getvalue()


# ---------------------------------------------------------------------------
# 3. Ninguna excepción sale como traza
# ---------------------------------------------------------------------------
def test_main_translates_an_unexpected_exception(tmp_path, monkeypatch, capsys) -> None:
    """La última red: `main` atrapa CUALQUIER excepción y devuelve UNEXPECTED.

    Traducir EACCES y ENOSPC uno a uno arregla los modos de fallo que se conocen
    hoy; lo que impide que el próximo imprevisto vuelva a salir como veinte
    líneas de traceback —sin «error:», sin código de la tabla documentada y sin
    ninguna indicación de qué hacer— es que `main` no deje escapar nada.
    """

    def explota(*_args, **_kwargs):
        raise RuntimeError("el disco se desmontó a mitad")

    monkeypatch.setattr("installer_backend.cli.run_generate", explota)

    code = main(["generate", "--config", _config_file(tmp_path)], out=io.StringIO())

    assert code == int(ExitCode.UNEXPECTED)
    err = capsys.readouterr().err
    assert "error inesperado" in err
    assert "RuntimeError" in err
    assert "el disco se desmontó a mitad" in err


# ---------------------------------------------------------------------------
# Los flags nuevos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["install", "generate"])
def test_force_new_secrets_is_available_on_both_writing_paths(command: str) -> None:
    """La puerta de emergencia existe donde se escribe el ``.env``, y sólo ahí."""

    args = build_parser().parse_args([command, "--config", "i.yaml", "--force-new-secrets"])

    assert args.force_new_secrets is True


def test_force_new_secrets_is_off_by_default() -> None:
    """Y está apagada: una puerta de emergencia abierta no es una puerta."""

    args = build_parser().parse_args(["generate", "--config", "i.yaml"])

    assert args.force_new_secrets is False


def test_the_unseal_keys_come_from_a_file_and_not_from_argv(tmp_path) -> None:
    """Un share de Shamir en la línea de comandos se ve en `ps` y en el historial.

    Por eso el flag pide un FICHERO. Es además el mismo formato que escribe el
    depósito de emergencia, así que reanudar una instalación interrumpida es
    apuntar el flag al fichero que el propio instalador dejó.
    """

    keys_file = tmp_path / "claves.txt"
    keys_file.write_text("unseal_key: uno\nunseal_key: dos\n", encoding="utf-8")

    args = build_parser().parse_args(
        ["install", "--config", "i.yaml", "--vault-unseal-keys-from", str(keys_file)]
    )

    assert args.vault_unseal_keys_from == str(keys_file)

    # Y no hay NINGÚN destino donde una clave pueda aterrizar desde argv.
    # `argparse` admite abreviaturas no ambiguas, así que `--vault-unseal-key uno`
    # no es un error de uso: se resuelve a este mismo flag y "uno" se interpreta
    # como la RUTA de un fichero — que falla después con un mensaje claro. Lo que
    # se afirma aquí es que la otra opción, la que sí guardaría el share, no
    # existe: si alguien la añade, este test cae.
    abreviado = build_parser().parse_args(
        ["install", "--config", "i.yaml", "--vault-unseal-key", "uno"]
    )
    assert abreviado.vault_unseal_keys_from == "uno"
    assert not hasattr(abreviado, "vault_unseal_key")


def test_the_keys_from_the_file_reach_the_executor(tmp_path, installer_config) -> None:
    keys_file = tmp_path / "claves.txt"
    keys_file.write_text("unseal_key: uno\nunseal_key: dos\n", encoding="utf-8")

    inst = build_default_installer(
        io.StringIO(),
        installer_config,
        inspector=_inspector({}),
        unseal_keys_path=str(keys_file),
    )

    executor = inst.executor
    assert isinstance(executor, RealStepExecutor)
    assert executor.existing_unseal_keys == ("uno", "dos")


def test_an_unreadable_unseal_keys_file_is_a_config_error(tmp_path, installer_config) -> None:
    """Apuntar mal el flag se dice ANTES de tocar la máquina, no a mitad."""

    with pytest.raises(CliError) as excinfo:
        build_default_installer(
            io.StringIO(),
            installer_config,
            inspector=_inspector({}),
            unseal_keys_path=str(tmp_path / "no-existe.txt"),
        )

    assert excinfo.value.code is ExitCode.CONFIG


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------
@dataclass
class _AlwaysOkPrereqs:
    def check_all(self) -> list[object]:
        return []


@dataclass
class _ScriptedCredentials:
    """Un :class:`CredentialBuilder` con avisos guionizados."""

    notes: tuple[str, ...] = ()

    def build(self, config: InstallerConfig):
        from installer_backend.finalize import InstallCredentials

        return InstallCredentials(
            admin_username=str(config.tenant.admin_email),
            admin_password="scripted",  # - placeholder, no es un secreto real
            vault_root_token="scripted",  # - placeholder
            vault_unseal_keys=("a", "b"),
        )

    def advisories(self) -> tuple[str, ...]:
        return self.notes


def _executor_with(cfg: InstallerConfig, *, vault_result: VaultBootstrapResult) -> RealStepExecutor:
    """Un ejecutor real con el resultado de Vault ya puesto (sin correr nada)."""

    from installer_backend.command_runner import FakeCommandRunner
    from installer_backend.config_generators import FakeDataTreeProvisioner
    from installer_backend.vault_bootstrap import FakeVaultClient

    ex = RealStepExecutor(
        compose_dir=_ROOT,
        runner=FakeCommandRunner(),
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        vault_client_factory=lambda _cfg: FakeVaultClient(),
        cfg=cfg,
        secrets=generate_secrets(),
    )
    ex.vault_bootstrap_result = vault_result
    ex.seeded_admin_password = "x"  # - placeholder, no es un secreto real
    return ex
