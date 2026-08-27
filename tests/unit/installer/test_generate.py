"""El subcomando ``generate`` — el instalador escribe y sale (ADR 0161, opción D).

La opción D existe por una razón concreta y no por elegancia: si el instalador
provisionara, necesitaría hablar con el daemon Docker, y montar su socket es
acceso root efectivo al host — exactamente lo que rechazó el ADR 0060. Tampoco
puede pasar por el socket-proxy, cuya ACL deniega ``VOLUMES``. Así que el
contenedor **no toca Docker**: se le monta sólo la raíz de datos, escribe el
árbol de arranque completo y sale. El ``up`` y la finalización los ejecuta el
operador.

Esa propiedad —«no toca Docker»— es la que sostiene todo el diseño, y es
invisible: nada se rompe el día que alguien añada un ``docker compose pull`` al
camino de ``generate``; simplemente el instalador dejaría de poder correr sin el
socket y nadie se enteraría hasta el despliegue. Por eso se afirma aquí con un
``CommandRunner`` que REVIENTA si alguien lo llama, en vez de con un fake que
cuenta llamadas y se pueda ignorar.

La segunda propiedad afirmada es que ``generate`` **no se puede cablear con los
seams de simulación**. El wizard HTTP miente hoy justamente porque su ejecutor
por defecto es el fake; un ``generate`` con el mismo agujero escribiría un log en
verde sobre una raíz de datos vacía, y el operador lo descubriría en el ``up``.
A diferencia de ``install``, aquí NO hay ``--dry-run`` que lo autorice: simular
una generación no tiene ningún valor —el resultado ES el árbol de ficheros—, así
que la guarda no tiene puerta trasera.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pytest
from installer_backend.cli import (
    BOOTSTRAP_SERVICE,
    BootTreeGenerator,
    CliError,
    ExitCode,
    build_default_generator,
    build_parser,
    main,
    run_generate,
)
from installer_backend.command_runner import CommandResult
from installer_backend.compose_generator import STACK_ASSETS_DIR_NAME, generate_compose
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import (
    FakeDataTreeProvisioner,
    FakeEnvFileWriter,
    GeneratedSecrets,
)
from installer_backend.install import (
    FakeStepExecutor,
    InstallStep,
    StepExecutionError,
    StepExecutor,
)
from installer_backend.real_step_executor import RealStepExecutor

pytestmark = pytest.mark.unit

_DATA_ROOT = "/data/agent-platform"

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


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------
@dataclass
class ExplodingCommandRunner:
    """Un :class:`CommandRunner` que REVIENTA en cuanto alguien lo llama.

    No cuenta llamadas para que el test las mire luego: aborta en el acto, con el
    argv en el mensaje. Un contador se puede dejar de comprobar en un refactor;
    una excepción, no.
    """

    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        argv = tuple(args)
        self.calls.append(argv)
        raise AssertionError(
            "`generate` ejecutó un comando externo, y no debe ejecutar NINGUNO: "
            f"{' '.join(argv)}. La opción D del ADR 0161 se sostiene sobre que el "
            "instalador no habla con el daemon Docker."
        )


@dataclass
class RecordingStepExecutor:
    """Envuelve un ejecutor real y anota QUÉ pasos se le pidieron, en orden."""

    inner: StepExecutor
    steps: list[InstallStep] = field(default_factory=list)

    def execute(self, step: InstallStep, config: dict[str, object]) -> list[str]:
        self.steps.append(step)
        return self.inner.execute(step, config)


@dataclass
class ExplodingStepExecutor:
    """Un ejecutor que falla el paso de generación (para el código de salida)."""

    message: str = "no se pudo escribir bajo la raíz de datos: permiso denegado"

    def execute(self, step: InstallStep, config: dict[str, object]) -> list[str]:
        raise StepExecutionError(self.message)


def _real_executor(
    cfg: InstallerConfig, secrets: GeneratedSecrets
) -> tuple[RealStepExecutor, ExplodingCommandRunner, FakeEnvFileWriter, FakeDataTreeProvisioner]:
    """Un :class:`RealStepExecutor` real salvo por el disco y por Docker.

    El escritor y el provisionador son fakes en memoria (el test no escribe en
    ``/data``); el runner de comandos revienta, que es la afirmación central.
    """

    runner = ExplodingCommandRunner()
    writer = FakeEnvFileWriter()
    tree = FakeDataTreeProvisioner()
    ex = RealStepExecutor(
        compose_dir=cfg.storage.data_root,
        runner=runner,
        env_writer=writer,
        tree=tree,
        vault_client_factory=lambda _cfg: pytest.fail(
            "`generate` construyó un cliente de Vault; el bootstrap corre DENTRO "
            "de la red del stack ya levantado, no aquí."
        ),
        cfg=cfg,
        secrets=secrets,
    )
    return ex, runner, writer, tree


def _write_config(tmp_path, text: str = _VALID_YAML) -> str:
    path = tmp_path / "install.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# La propiedad que sostiene la opción D
# ---------------------------------------------------------------------------
def test_generate_no_invoca_a_docker(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """NADA de Docker: ni pull, ni up, ni migraciones, ni siembra.

    Es LA propiedad de la opción D: sin ella el contenedor necesitaría el socket
    del daemon y volveríamos al choque con el ADR 0060. Si nadie la afirma, se
    pierde en el primer refactor que «aproveche» que el ejecutor ya sabe llamar a
    compose.
    """

    executor, runner, _writer, _tree = _real_executor(installer_config, gen_secrets)
    out = io.StringIO()

    code = run_generate(
        _write_config(tmp_path),
        generator=BootTreeGenerator(executor=executor, out=out),
        out=out,
    )

    assert int(code) == int(ExitCode.OK)
    assert runner.calls == [], "`generate` no debe ejecutar ningún comando externo"

    # Control positivo: que la lista esté vacía sólo significa algo si la trampa
    # estaba armada. Un runner mal cableado daría exactamente el mismo verde, y
    # este test pasaría para siempre sin afirmar nada. Aquí se comprueba que el
    # MISMO ejecutor, pedido cualquier otro paso, sí revienta.
    with pytest.raises(AssertionError, match="ejecutó un comando externo"):
        executor.execute(InstallStep.PULL_IMAGES, {})


def test_generate_ejecuta_solo_el_paso_de_generacion(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Un solo paso del pipeline: ``generate_config``. Los otros cinco, ninguno."""

    executor, _runner, _writer, _tree = _real_executor(installer_config, gen_secrets)
    recorder = RecordingStepExecutor(inner=executor)
    out = io.StringIO()
    generator = BootTreeGenerator(executor=recorder, out=out)

    run_generate(_write_config(tmp_path), generator=generator, out=out)

    assert recorder.steps == [InstallStep.GENERATE_CONFIG]
    assert generator.phases == [InstallStep.GENERATE_CONFIG.value]


def test_generate_no_revela_ninguna_credencial(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Sin finalize no hay revelado: las credenciales nacen en el paso siguiente.

    En la opción D el root token de Vault y la contraseña del admin los produce el
    one-shot de finalización dentro de la red del stack, no este contenedor. Un
    ``generate`` que imprimiera algo parecido a un secreto sería una regresión
    silenciosa: nadie lee la salida de un comando que terminó en verde.
    """

    executor, _runner, _writer, _tree = _real_executor(installer_config, gen_secrets)
    out = io.StringIO()

    run_generate(
        _write_config(tmp_path),
        generator=BootTreeGenerator(executor=executor, out=out),
        out=out,
    )

    printed = out.getvalue()
    assert "Unseal key" not in printed
    assert gen_secrets.postgres_password not in printed
    assert installer_config.storage.minio_secret_key.get_secret_value() not in printed


# ---------------------------------------------------------------------------
# Lo que sí escribe
# ---------------------------------------------------------------------------
def test_generate_escribe_el_arbol_de_arranque_completo(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Compose, ``.env``, ``config/global.yaml``, el Caddyfile y los auxiliares.

    Los cinco, porque el compose generado los MONTA: a un bind cuyo lado host no
    existe, Docker le inventa un directorio vacío, así que faltar uno no da error
    — da un Postgres sin ``pgvector`` o un Vault que encuentra un directorio donde
    espera su fichero de configuración.
    """

    executor, _runner, writer, tree = _real_executor(installer_config, gen_secrets)
    out = io.StringIO()

    run_generate(
        _write_config(tmp_path),
        generator=BootTreeGenerator(executor=executor, out=out),
        out=out,
    )

    root = installer_config.storage.data_root
    assert f"{root}/docker-compose.yml" in writer.written
    assert writer.modes[f"{root}/.env"] == 0o600
    assert f"{root}/config/global.yaml" in writer.written
    assert f"{root}/caddy/Caddyfile" in writer.written
    # Los auxiliares que el PR #124 añadió, bajo `stack/`.
    assert f"{root}/{STACK_ASSETS_DIR_NAME}/postgres/init/01-extensions.sql" in writer.written
    assert f"{root}/{STACK_ASSETS_DIR_NAME}/vault/config.hcl" in writer.written
    assert tree.provisioned, "no se provisionó el árbol de datos"


# ---------------------------------------------------------------------------
# La guarda anti-simulación: aquí NO hay --dry-run que la abra
# ---------------------------------------------------------------------------
def test_generate_rechaza_los_seams_de_simulacion(tmp_path) -> None:
    """Un ``FakeStepExecutor`` cableado en ``generate`` aborta, sin excepción.

    Es lo que hoy hace mentir al wizard: ejecutor fake, log en verde, cero
    ficheros. ``generate`` no tiene ``--dry-run``, así que la guarda no tiene
    puerta trasera — simular la escritura de un árbol de ficheros no informa de
    nada, porque el árbol ES el resultado.
    """

    out = io.StringIO()
    with pytest.raises(CliError) as excinfo:
        run_generate(
            _write_config(tmp_path),
            generator=BootTreeGenerator(executor=FakeStepExecutor(), out=out),
            out=out,
        )
    assert excinfo.value.code is ExitCode.GENERATE
    assert "FakeStepExecutor" in str(excinfo.value)


def test_el_generador_por_defecto_cablea_el_ejecutor_real(
    installer_config: InstallerConfig,
) -> None:
    """Sin inyección, ``generate`` usa el ejecutor real (construir no toca nada)."""

    generator = build_default_generator(io.StringIO(), installer_config)
    assert isinstance(generator.executor, RealStepExecutor)
    assert generator.executor.compose_dir == installer_config.storage.data_root


def test_generate_no_acepta_dry_run() -> None:
    """``--dry-run`` en ``generate`` es un error de uso, no una simulación."""

    with pytest.raises(SystemExit):
        build_parser().parse_args(["generate", "--config", "install.yaml", "--dry-run"])


# ---------------------------------------------------------------------------
# La salida: el diseño D convierte una línea en tres, y hay que decirlo
# ---------------------------------------------------------------------------
def test_generate_anuncia_los_dos_comandos_que_faltan(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El operador tiene que saber que quedan DOS comandos suyos por ejecutar.

    Un instalador que termina en verde sin decirlo deja un stack que no existe y
    un operador convencido de lo contrario.
    """

    executor, _runner, _writer, _tree = _real_executor(installer_config, gen_secrets)
    out = io.StringIO()

    run_generate(
        _write_config(tmp_path),
        generator=BootTreeGenerator(executor=executor, out=out),
        out=out,
    )

    printed = out.getvalue()
    root = installer_config.storage.data_root
    assert f"cd {root} && docker compose up -d --wait" in printed
    assert f"docker compose run --rm {BOOTSTRAP_SERVICE}" in printed


def test_el_bootstrap_que_el_banner_anuncia_sigue_sin_existir_en_el_compose(
    installer_config: InstallerConfig,
) -> None:
    """Guarda-alambre: el banner nombra un servicio que el compose aún NO declara.

    El paso 8 del ADR 0161 son dos mitades — el subcomando ``generate`` y el
    one-shot de finalización dentro de la red del stack — y esta es la única
    costura donde se tocan. Sin esta guarda, la deuda vive en un comentario que
    nadie lee y el operador se come un ``no such service: bootstrap``.
    """

    services = generate_compose(installer_config)["services"]
    assert BOOTSTRAP_SERVICE not in services, (
        f"El compose generado ya declara «{BOOTSTRAP_SERVICE}»: la otra mitad del "
        "paso 8 del ADR 0161 está hecha. Da la vuelta a esta guarda (`in` en vez "
        "de `not in`) — deja de ser una deuda anotada y pasa a ser un contrato "
        "comprobable entre el banner y el compose."
    )


# ---------------------------------------------------------------------------
# Códigos de salida
# ---------------------------------------------------------------------------
def test_un_fallo_de_generacion_tiene_su_propio_codigo_de_salida(tmp_path) -> None:
    """``GENERATE`` (6) y no ``PROVISION`` (4): no se aprovisionó nada.

    La distinción es operativa, no cosmética: un 4 dice «el stack puede haber
    quedado a medias»; un 6 dice «no se levantó nada, la raíz de datos puede
    tener escrituras parciales». La automatización del operador ramifica distinto.
    """

    out = io.StringIO()
    with pytest.raises(CliError) as excinfo:
        run_generate(
            _write_config(tmp_path),
            generator=BootTreeGenerator(executor=ExplodingStepExecutor(), out=out),
            out=out,
        )
    assert excinfo.value.code is ExitCode.GENERATE
    assert int(ExitCode.GENERATE) not in (
        int(ExitCode.OK),
        int(ExitCode.USAGE),
        int(ExitCode.CONFIG),
        int(ExitCode.PREREQ),
        int(ExitCode.PROVISION),
        int(ExitCode.ABORTED),
    )
    assert "permiso denegado" in str(excinfo.value)


def test_un_yaml_invalido_se_rechaza_antes_de_escribir_nada(tmp_path) -> None:
    """La puerta de configuración va primero: CONFIG (2) y cero escrituras."""

    out = io.StringIO()
    bad = tmp_path / "install.yaml"
    bad.write_text("system: [esto no es un mapping]\n", encoding="utf-8")
    with pytest.raises(CliError) as excinfo:
        run_generate(str(bad), out=out)
    assert excinfo.value.code is ExitCode.CONFIG


def test_main_cablea_el_subcomando_generate(tmp_path) -> None:
    """``main`` despacha ``generate`` y mapea su CliError al código de salida.

    Se usa un fichero inexistente para probar el despacho sin tocar la raíz de
    datos real: un comando desconocido saldría con USAGE (1); llegar a CONFIG (2)
    demuestra que el subcomando existe y que se ejecutó su lectura de config.
    """

    args = build_parser().parse_args(["generate", "--config", "install.yaml"])
    assert args.command == "generate"
    assert args.config == "install.yaml"

    missing = str(tmp_path / "no-existe.yaml")
    assert main(["generate", "--config", missing], out=io.StringIO()) == int(ExitCode.CONFIG)
