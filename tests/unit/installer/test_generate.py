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
from pathlib import Path

import pytest
from installer_backend.cli import (
    BOOTSTRAP_ENTRYPOINT_AVAILABLE,
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
from installer_backend.compose_generator import (
    BOOTSTRAP_ENTRYPOINT,
    STACK_ASSETS_DIR_NAME,
    generate_compose,
)
from installer_backend.compose_generator import (
    BOOTSTRAP_SERVICE as COMPOSE_BOOTSTRAP_SERVICE,
)
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
from installer_backend.prereqs import (
    BYTES_PER_GIB,
    DEFAULT_MIN_DISK_GIB,
    DEFAULT_MIN_RAM_GIB,
    MIN_COMPOSE_VERSION,
    MIN_DOCKER_VERSION,
    REQUIRED_FREE_PORTS,
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
def _generate(tmp_path, cfg: InstallerConfig, secrets: GeneratedSecrets, **kwargs) -> str:
    """Corre ``generate`` con el ejecutor real-salvo-disco y devuelve lo impreso."""

    executor, _runner, _writer, _tree = _real_executor(cfg, secrets)
    out = io.StringIO()
    run_generate(
        _write_config(tmp_path),
        generator=BootTreeGenerator(executor=executor, out=out, **kwargs),
        out=out,
    )
    return out.getvalue()


def test_generate_anuncia_el_comando_que_de_verdad_falta(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El primer comando SIEMPRE se anuncia: sin él no hay stack.

    Un instalador que termina en verde sin decirlo deja un stack que no existe y
    un operador convencido de lo contrario.
    """

    printed = _generate(tmp_path, installer_config, gen_secrets)

    root = installer_config.storage.data_root
    assert f"cd {root} && docker compose up -d --wait" in printed


def test_el_banner_solo_manda_ejecutar_el_bootstrap_si_ese_bootstrap_existe(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El banner sólo da la orden si el one-shot existe. Desde el 2026-08-28, la da.

    Este test cambia de rama solo con `BOOTSTRAP_ENTRYPOINT_AVAILABLE`, y esa
    bandera la mueve —a la fuerza—
    `test_la_disponibilidad_del_bootstrap_declarada_coincide_con_el_arbol`. Con
    `api_server.bootstrap` ya en el árbol, lo que se afirma aquí es que el banner
    imprime `docker compose run --rm bootstrap`; la otra rama sigue escrita
    porque la regla no era «esperar a ese módulo», era «no mandar ejecutar lo que
    no existe», y el día que alguien lo borre el banner tiene que volver a
    decirlo.

    Éste es el test que fijó el arreglo, y nació al revés: antes afirmaba que el
    banner imprimía `docker compose run --rm bootstrap` **sin ninguna reserva**,
    así que la suite en verde certificaba el agujero. Lo que recibía el operador
    era un stack `Up (healthy)` —el healthcheck de Vault acepta a propósito un
    Vault sellado— con Vault sin inicializar, sin tenant, sin usuario admin y sin
    credenciales, después de que el instalador le hubiera dicho que ese comando
    le iba a dar todas esas cosas. Un banner que manda ejecutar algo que falla es
    peor que no imprimir nada: convierte «falta media tarea» en «tu Docker está
    roto».

    **Corregido el 2026-08-28**, y la corrección es la mitad interesante. Este
    test exigía que el banner rematara con «termina desde el host con
    `installer_backend.cli install`», descrito como «el camino que SÍ termina
    hoy». No terminaba: el paso 4 del `install` hablaba con Vault contra
    `127.0.0.1:8200` y el servicio `vault` del compose generado no publica ningún
    puerto —el único que publica es Caddy (ADR 0061)—, así que moría con una
    traza cruda. O sea que la suite en verde certificaba, otra vez, una salida de
    emergencia rota; sólo que esta vez la que se ofrecía en lugar de la orden que
    sí se había retirado. Desde que el `install` delega en este mismo one-shot,
    las dos mitades comparten destino, y lo que el banner tiene que decir es eso.
    """

    printed = _generate(tmp_path, installer_config, gen_secrets)
    orden = f"docker compose run --rm {BOOTSTRAP_SERVICE}"

    if BOOTSTRAP_ENTRYPOINT_AVAILABLE:
        assert orden in printed
        return

    # La orden puede aparecer NOMBRADA (para decir que no está disponible), pero
    # nunca como uno de los pasos que el operador debe ejecutar.
    assert "NO DISPONIBLE" in printed or "no disponible" in printed
    assert BOOTSTRAP_ENTRYPOINT in printed, (
        "hay que nombrar el módulo que falta: es lo que convierte el error en un "
        "diagnóstico en vez de en una sospecha sobre el Docker del operador"
    )
    # Y NO puede ofrecer el `install` desde el host como si lo supliera: ejecuta
    # exactamente este mismo one-shot, así que hoy muere en el mismo sitio.
    assert "installer_backend.cli install --config" not in printed, (
        "mandar al operador a gastar una instalación entera para llegar al mismo "
        "punto muerto es peor que no ofrecer salida: cuesta una instalación"
    )
    assert "NINGÚN camino" in printed or "ninguno de los dos caminos" in printed.lower(), (
        "si la finalización no está disponible por ninguna vía, hay que decirlo: "
        "un banner que insinúa que hay otra deja al operador buscándola"
    )


def test_la_disponibilidad_del_bootstrap_declarada_coincide_con_el_arbol() -> None:
    """La bandera del banner y el árbol del repositorio no pueden discrepar.

    `BOOTSTRAP_ENTRYPOINT_AVAILABLE` es una declaración escrita a mano, y una
    declaración a mano envejece: si alguien aterriza `api_server.bootstrap` y no
    la mueve, el banner seguiría diciendo «no disponible» de algo que ya
    funciona; si alguien la mueve antes de tiempo, vuelve el callejón sin salida.
    Aquí se cruza contra el sitio donde el módulo tiene que vivir, derivado del
    propio `BOOTSTRAP_ENTRYPOINT` para que un renombrado no deje la guarda
    apuntando a una ruta muerta.

    **Disparó el 2026-08-28**, que es para lo que estaba: el módulo aterrizó, la
    suite se puso roja, y la bandera pasó a `True` junto con el banner y la
    documentación. Sigue vigilando el sentido contrario.
    """

    repo = Path(__file__).resolve().parents[3]
    module_dir = repo / "apps" / "api-server" / "src" / Path(*BOOTSTRAP_ENTRYPOINT.split("."))
    on_disk = (module_dir / "__main__.py").is_file() or module_dir.with_suffix(".py").is_file()

    assert on_disk == BOOTSTRAP_ENTRYPOINT_AVAILABLE, (
        f"BOOTSTRAP_ENTRYPOINT_AVAILABLE={BOOTSTRAP_ENTRYPOINT_AVAILABLE} pero el "
        f"módulo {BOOTSTRAP_ENTRYPOINT} {'SÍ' if on_disk else 'NO'} está en "
        f"{module_dir}. Si acabas de aterrizar la segunda mitad del paso 8 del "
        "ADR 0161, mueve la bandera en cli.py: el banner volverá a dar la orden."
    )


def test_el_bootstrap_que_el_banner_anuncia_existe_en_el_compose(
    installer_config: InstallerConfig,
) -> None:
    """El banner y el compose nombran el MISMO servicio, y ese servicio existe.

    Esta guarda nació al revés: afirmaba que el compose **no** lo declaraba, y su
    propio mensaje pedía darle la vuelta el día que aterrizara la otra mitad del
    paso 8 del ADR 0161. Ese día es hoy (auditoría 2026-08-27), así que deja de
    ser una deuda anotada y pasa a ser el contrato que impide la avería: el
    banner manda ejecutar `docker compose run --rm bootstrap` sin ninguna
    reserva, y con el servicio ausente lo que recibía el operador era
    `no such service: bootstrap` sobre un stack `Up (healthy)` con Vault sellado
    y sin inicializar, sin tenant y sin usuario admin. La instalación parece
    terminada y no lo está.

    Es la ÚNICA costura donde el banner (CLI) y el generador de compose se tocan,
    por eso se afirma aquí y con el símbolo, no con la cadena "bootstrap".
    """

    services = generate_compose(installer_config)["services"]
    assert BOOTSTRAP_SERVICE in services, (
        f"El banner manda ejecutar «{BOOTSTRAP_SERVICE}» y el compose generado no "
        "lo declara: el operador recibe `no such service` y se queda con Vault sin "
        "inicializar, sin tenant y sin credenciales, creyendo que ha terminado."
    )
    # Y que exista no basta: `up -d --wait` no debe arrancarlo. Un one-shot sin
    # perfil se relanzaría en cada arranque del host, y `--wait` esperaría a un
    # contenedor que sale.
    assert services[BOOTSTRAP_SERVICE].get("profiles") == [BOOTSTRAP_SERVICE]


def test_el_banner_y_el_compose_no_pueden_nombrar_servicios_distintos() -> None:
    """Los dos módulos declaran el nombre por su cuenta; aquí se cruzan.

    `cli.BOOTSTRAP_SERVICE` (lo que el banner IMPRIME) y
    `compose_generator.BOOTSTRAP_SERVICE` (lo que el compose DECLARA) son dos
    constantes distintas en dos ficheros distintos. Mientras coincidan, el
    comando que lee el operador existe; si alguien renombra una, el `no such
    service` vuelve exactamente igual que antes y sin que nada más falle.
    """

    assert BOOTSTRAP_SERVICE == COMPOSE_BOOTSTRAP_SERVICE


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


# ---------------------------------------------------------------------------
# Los prerequisitos: `generate` no es una puerta, pero tampoco puede callarse
# ---------------------------------------------------------------------------
def test_el_banner_lista_los_prerequisitos_que_nadie_comprueba_en_este_camino(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Lo que el contenedor no puede ver, se lo dice al operador para que lo vea él.

    `generate` no habla con Docker —es LA propiedad de la opción D— así que no
    puede sondear el daemon, ni la versión de Compose, ni los puertos del host
    desde su propia netns. Pero las comprobaciones existen, con mensajes de
    remediación buenos y en castellano, y en este camino no las corría nadie: el
    operador se enteraba de que nginx tenía el 443 al ejecutar `up -d --wait`,
    con parte del stack ya levantada. Un instalador que sabe la comprobación y no
    la enseña es peor que uno que no la tiene.

    Los umbrales salen de `prereqs.py`, no de literales aquí: si alguien sube el
    mínimo de RAM, el banner lo sigue solo.
    """

    printed = _generate(tmp_path, installer_config, gen_secrets)

    assert str(REQUIRED_FREE_PORTS[0]) in printed
    assert str(REQUIRED_FREE_PORTS[1]) in printed
    assert f"{MIN_COMPOSE_VERSION[0]}.{MIN_COMPOSE_VERSION[1]}" in printed
    assert f"{MIN_DOCKER_VERSION[0]}.{MIN_DOCKER_VERSION[1]}" in printed
    assert f"{DEFAULT_MIN_RAM_GIB}" in printed


def test_el_disco_libre_si_se_puede_medir_desde_dentro_y_se_mide(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """La raíz de datos está MONTADA: su disco libre es el del host, y es medible.

    Es la mitad de la puerta de prerequisitos que sí vale desde dentro del
    contenedor, así que se ejecuta de verdad en vez de listarse. Se emite como
    AVISO y no como puerta: escribir el árbol de arranque en una máquina a la que
    se le va a montar un disco mayor es legítimo, y abortar ahí sería inventar un
    bloqueo que el `install` desde el host tampoco impone en este punto.
    """

    poco = 3 * BYTES_PER_GIB
    printed = _generate(tmp_path, installer_config, gen_secrets, free_disk_probe=lambda _p: poco)

    assert "AVISO" in printed
    assert "3.0" in printed, printed
    assert str(DEFAULT_MIN_DISK_GIB) in printed


def test_con_disco_de_sobra_no_se_inventa_un_aviso(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Control negativo: un aviso que sale siempre deja de leerse."""

    de_sobra = (DEFAULT_MIN_DISK_GIB + 100) * BYTES_PER_GIB
    printed = _generate(
        tmp_path, installer_config, gen_secrets, free_disk_probe=lambda _p: de_sobra
    )

    assert "AVISO: disco" not in printed


def test_una_sonda_de_disco_que_no_puede_medir_no_rompe_la_generacion(
    tmp_path, installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Si no se puede medir, se dice; lo que no se hace es fallar por ello.

    El entregable de este subcomando es el árbol de ficheros. Que una sonda
    informativa no sepa contestar no puede impedir escribirlo.
    """

    def revienta(_path: str) -> int:
        raise OSError("no se puede medir")

    printed = _generate(tmp_path, installer_config, gen_secrets, free_disk_probe=revienta)

    assert f"cd {installer_config.storage.data_root} && docker compose up" in printed
