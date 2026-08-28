"""Unit tests for the real install StepExecutor (Plan prod-01 task_16 / deploy-1).

`RealStepExecutor` is the binding that turns the install pipeline from a
simulacrum into a real provisioner. Driven here against in-memory fakes
(FakeCommandRunner / FakeEnvFileWriter / FakeDataTreeProvisioner / FakeEscrowFile)
so the ORCHESTRATION — which files are written, the docker compose argv + order,
fail propagation, la finalización, la siembra — is verified WITHOUT a Docker host.
The real subprocess calls are exercised only by the e2e / human tests.

Ya no hay `FakeVaultClient` aquí, y no es una simplificación del test: desde el
ADR 0161 el instalador **no habla con Vault**. El paso BOOTSTRAP_VAULT delega en
el one-shot `bootstrap` del compose generado, que corre DENTRO de `agentic-net`,
y lo único que este ejecutor hace con Vault es leer lo que ese one-shot le cuenta
por stdout. Lo que se guioniza aquí, por tanto, es esa salida.
"""

from __future__ import annotations

import errno
import json
import re
from dataclasses import dataclass, field

import pytest
from installer_backend.command_runner import CommandResult, FakeCommandRunner
from installer_backend.compose_generator import (
    BOOTSTRAP_ENTRYPOINT,
    BOOTSTRAP_SERVICE,
    PROJECT_NAME,
)
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import (
    FakeDataTreeProvisioner,
    FakeEnvFileWriter,
    GeneratedSecrets,
)
from installer_backend.install import InstallStep, StepExecutionError, StepExecutor
from installer_backend.install_state import FakeFileReader
from installer_backend.key_escrow import (
    UNSEAL_KEYS_FILENAME,
    FakeEscrowFile,
    FileKeyEscrow,
)
from installer_backend.real_step_executor import (
    BOOTSTRAP_REVEAL_EVENT,
    BOOTSTRAP_UNSEAL_KEYS_ENV,
    BOOTSTRAP_UNSEAL_KEYS_SEPARATOR,
    RealStepExecutor,
)

pytestmark = pytest.mark.unit

_COMPOSE_DIR = "/srv/agentic"
_COMPOSE_FILE = f"{_COMPOSE_DIR}/docker-compose.yml"

# ---------------------------------------------------------------------------
# El one-shot de finalización: el contrato de su stdout, guionizado
# ---------------------------------------------------------------------------
#: El argv EXACTO que el paso BOOTSTRAP_VAULT tiene que emitir. Es —sin el `-p`
#: y el `-f`, que el instalador conoce y el operador no— el MISMO comando que el
#: banner de `generate` le deja escrito: `docker compose run --rm bootstrap`.
#: Que sean el mismo es el punto entero del cambio, así que se afirma literal.
_BOOTSTRAP_ARGV = (
    "docker",
    "compose",
    "-p",
    PROJECT_NAME,
    "-f",
    _COMPOSE_FILE,
    "run",
    "--rm",
    BOOTSTRAP_SERVICE,
)

#: Valores de mentira con forma de secreto. Se afirma que NINGUNO sale por el log
#: de progreso, así que tienen que ser reconocibles a simple vista en un diff.
_ROOT_TOKEN = "hvs.token-de-mentira-para-tests"
_ADMIN_PASSWORD = "contrasena-de-mentira"
_UNSEAL_KEYS = tuple(f"share-de-mentira-{i}" for i in range(1, 6))


def _reveal_line(**overrides: object) -> str:
    """La línea de revelado del one-shot, con el prefijo que antepone Compose.

    El prefijo (`bootstrap-1  | `) NO es decorado: es lo que se ve de verdad en
    la salida de `docker compose run`, y el parser tiene que sobrevivirlo. Es el
    mismo motivo por el que `_admin_user_existed_from` busca las llaves dentro
    de la línea en vez de hacer `json.loads` de la línea entera.
    """

    payload: dict[str, object] = {
        "event": BOOTSTRAP_REVEAL_EVENT,
        "already_initialized": False,
        "unseal_keys": list(_UNSEAL_KEYS),
        "root_token": _ROOT_TOKEN,
        "key_threshold": 3,
        "kv_mount": "secret",
        "kv_enabled": True,
        "policies_written": ["api-server", "workers", "orchestrator", "notification"],
        "admin_password": _ADMIN_PASSWORD,
        "admin_user_created": True,
    }
    payload.update(overrides)
    return "bootstrap-1  | " + json.dumps(payload)


def _bootstrap_argv(*passthrough: str) -> tuple[str, ...]:
    """El argv del one-shot, con los flags `-e` de paso a través que toquen."""

    head = _BOOTSTRAP_ARGV[:-1]
    return (*head, *passthrough, _BOOTSTRAP_ARGV[-1])


def _bootstrap_runner(
    *,
    rc: int = 0,
    before: tuple[str, ...] = (),
    reveal: bool = True,
    argv: tuple[str, ...] = _BOOTSTRAP_ARGV,
    **overrides: object,
) -> FakeCommandRunner:
    """Un runner que guioniza la salida del one-shot para el argv de arriba."""

    lines = (*before, *((_reveal_line(**overrides),) if reveal else ()))
    return FakeCommandRunner(responses={argv: CommandResult(rc, lines)})


def _executor(
    cfg: InstallerConfig,
    secrets: GeneratedSecrets,
    *,
    runner: FakeCommandRunner | None = None,
) -> tuple[RealStepExecutor, FakeCommandRunner, FakeEnvFileWriter, FakeDataTreeProvisioner]:
    runner = runner or _bootstrap_runner()
    writer = FakeEnvFileWriter()
    tree = FakeDataTreeProvisioner()
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=runner,
        env_writer=writer,
        tree=tree,
        cfg=cfg,
        secrets=secrets,
    )
    return ex, runner, writer, tree


def test_real_executor_satisfies_the_step_executor_protocol(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, *_ = _executor(installer_config, gen_secrets)
    assert isinstance(ex, StepExecutor)


def test_generate_config_writes_the_generated_files_with_modes(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, _runner, writer, tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.GENERATE_CONFIG, {})

    assert writer.modes[f"{_COMPOSE_DIR}/docker-compose.yml"] == 0o640
    assert writer.modes[f"{_COMPOSE_DIR}/.env"] == 0o600
    assert writer.modes[f"{_COMPOSE_DIR}/config/global.yaml"] == 0o640
    assert writer.modes[f"{_COMPOSE_DIR}/caddy/Caddyfile"] == 0o644
    # The Caddyfile must exist before `up` (the compose bind-mounts it).
    assert "reverse_proxy admin-panel:3000" in writer.written[f"{_COMPOSE_DIR}/caddy/Caddyfile"]
    # The /data tree was provisioned.
    assert tree.provisioned, "data tree was not provisioned"


def test_generate_config_also_lays_down_the_auxiliaries_the_compose_mounts(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Los auxiliares que el compose monta se escriben, y con su contenido real.

    Hasta el 2026-08-27 este paso escribía cuatro ficheros y el compose montaba
    seis familias de rutas más. Faltar no daba error: Docker inventa el lado host
    ausente de un bind como directorio vacío, así que Postgres nacía sin
    `pgvector` ni roles y Vault encontraba un directorio donde espera su config.
    """

    ex, _runner, writer, _tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.GENERATE_CONFIG, {})

    init_sql = writer.written[f"{_COMPOSE_DIR}/stack/postgres/init/01-extensions.sql"]
    assert "CREATE EXTENSION IF NOT EXISTS vector" in init_sql

    # Los `.sh` van ejecutables: el entrypoint de Postgres hace `source` de un
    # `.sh` que no lo es, y eso mete su `set -euo pipefail` en el shell que
    # sigue corriendo después.
    assert writer.modes[f"{_COMPOSE_DIR}/stack/postgres/init/02-roles.sh"] == 0o755
    # Config: legible por el proceso de dentro del contenedor, que no corre como
    # el usuario que instaló.
    assert writer.modes[f"{_COMPOSE_DIR}/stack/vault/config.hcl"] == 0o644
    assert 'storage "file"' in writer.written[f"{_COMPOSE_DIR}/stack/vault/config.hcl"]
    # Los dos contextos de build de los servicios del NÚCLEO.
    for proxy in ("egress-proxy", "registry-proxy"):
        assert f"{_COMPOSE_DIR}/stack/{proxy}/Dockerfile" in writer.written
        assert f"{_COMPOSE_DIR}/stack/{proxy}/filter.txt" in writer.written
    assert f"{_COMPOSE_DIR}/stack/seccomp/agent-runtime.json" in writer.written

    # Sin la superposición de observabilidad no se escribe su configuración.
    assert not [path for path in writer.written if "/stack/monitoring/" in path]


def test_generate_config_lays_down_the_monitoring_auxiliaries_only_with_the_overlay(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Con `monitoring=True` el generador emite Prometheus/Alertmanager/Grafana…

    …y sus tres binds de configuración. El buzón de credenciales del receiver de
    respaldo no lleva ficheros, así que no puede salir de un `write`: sale del
    plan de directorios, o Docker lo crearía como root y Alertmanager —que corre
    como `nobody`— fallaría al notificar, en silencio.
    """

    runner = FakeCommandRunner()
    writer = FakeEnvFileWriter()
    tree = FakeDataTreeProvisioner()
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=runner,
        env_writer=writer,
        tree=tree,
        cfg=installer_config,
        secrets=gen_secrets,
        monitoring=True,
    )
    ex.execute(InstallStep.GENERATE_CONFIG, {})

    assert f"{_COMPOSE_DIR}/stack/monitoring/prometheus/prometheus.yml" in writer.written
    assert f"{_COMPOSE_DIR}/stack/monitoring/prometheus/rules/host_alerts.yml" in writer.written
    assert f"{_COMPOSE_DIR}/stack/monitoring/alertmanager/alertmanager.yml" in writer.written
    buzon = f"{_COMPOSE_DIR}/stack/monitoring/alertmanager/secrets"
    assert buzon in [entry.path for entry in tree.provisioned]


def test_generate_config_env_carries_no_dev_secret_marker_in_prod(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, _runner, writer, _tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.GENERATE_CONFIG, {})
    env_text = writer.written[f"{_COMPOSE_DIR}/.env"]
    # El .env de producción no puede llevar un default de desarrollo
    # `${VAR:-valor}`. Se busca el PATRÓN, no el substring `:-` suelto: los
    # secretos son `token_urlsafe`, que empieza por `-` una vez de cada 64, y
    # dentro de una URL (`usuario:CONTRASEÑA@host`) eso produce `:-` sin que
    # haya ningún default. Era un test que fallaba ~1 de cada 10 ejecuciones
    # por azar y le echaba la culpa al commit que tocara.
    assert not re.search(r"\$\{[^}]+:-", env_text), env_text


def test_docker_steps_issue_expected_argv_in_order(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, runner, _writer, _tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.PULL_IMAGES, {})
    ex.execute(InstallStep.START_STACK, {})
    ex.execute(InstallStep.RUN_MIGRATIONS, {})

    prefix = ("docker", "compose", "-p", PROJECT_NAME, "-f", _COMPOSE_FILE)
    assert runner.calls[0] == (*prefix, "pull")
    assert runner.calls[1] == (*prefix, "up", "-d", "--wait")
    assert runner.calls[2] == (*prefix, "run", "--rm", "migrations")
    # All ran with cwd == compose_dir.
    assert runner.cwds == [_COMPOSE_DIR, _COMPOSE_DIR, _COMPOSE_DIR]


def test_a_failing_docker_step_raises_step_execution_error(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    runner = FakeCommandRunner(
        fail_on=("docker", "compose", "-p", PROJECT_NAME, "-f", _COMPOSE_FILE, "up")
    )
    ex, _runner, _writer, _tree = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError):
        ex.execute(InstallStep.START_STACK, {})


def test_el_bootstrap_corre_dentro_del_stack_y_no_contra_el_host(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El paso 4 delega en el one-shot del compose; no habla con Vault desde aquí.

    Éste es el test del último bloqueante del camino de instalación. El paso
    hablaba con Vault por HTTP desde el HOST —`real_bindings.build_hvac_vault_client`
    contra `http://127.0.0.1:8200`—, y el servicio `vault` del compose generado
    **no publica ningún puerto**: el único que publica es Caddy (80/443, ADR
    0061), y Caddy sólo enruta api-server y admin-panel. O sea: aunque las
    imágenes existieran, la instalación moría en el paso 4 con un
    `ConnectionRefusedError` que —al no ser `VaultBootstrapError`— salía como
    traza cruda de Python.

    El arreglo NO es publicar el 8200: sería ampliar la superficie publicada
    para ahorrarse un rediseño. Es el que ya decidió el ADR 0161: «el bootstrap
    de Vault y la siembra corren dentro de la red del stack ya levantado, que es
    donde tienen que correr».
    """

    ex, runner, *_ = _executor(installer_config, gen_secrets)

    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert runner.calls == [_BOOTSTRAP_ARGV], (
        "el paso tiene que ejecutar EXACTAMENTE el one-shot del compose, que es "
        "el mismo comando que el banner de `generate` le deja al operador"
    )
    assert runner.cwds == [_COMPOSE_DIR]
    assert ex.vault_bootstrap_result is not None
    assert ex.vault_bootstrap_result.init is not None  # init fresco
    assert ex.vault_bootstrap_result.init.root_token == _ROOT_TOKEN
    assert ex.vault_bootstrap_result.init.unseal_keys == _UNSEAL_KEYS
    assert ex.vault_bootstrap_result.kv_enabled is True
    assert ex.vault_bootstrap_result.kv_mount == "secret"
    assert len(ex.vault_bootstrap_result.policies_written) == 4


def test_el_install_y_el_banner_ejecutan_LITERALMENTE_el_mismo_comando(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """La unificación de los dos caminos, afirmada donde puede derivar.

    El `generate` sin clon imprime `docker compose run --rm bootstrap` y lo
    ejecuta el operador; el `install` desde el host lo ejecuta por él. Si el
    ejecutor añadiera un flag, cambiara el servicio o metiera un override de
    comando, volverían a ser dos finalizaciones distintas —y la que se prueba en
    los tests no sería la que el operador ejecuta a mano.
    """

    from installer_backend.cli import BOOTSTRAP_SERVICE as BANNER_SERVICE

    ex, runner, *_ = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    orden_del_banner = f"docker compose run --rm {BANNER_SERVICE}"
    ejecutado = " ".join(runner.calls[0])
    # Lo único que el instalador añade es el `-p`/`-f` que el operador no
    # necesita porque ya está dentro del directorio del compose.
    assert ejecutado.startswith("docker compose -p ")
    assert ejecutado.endswith("run --rm " + BANNER_SERVICE)
    assert orden_del_banner.split(" run ")[1] == ejecutado.split(" run ")[1]


def test_el_revelado_del_one_shot_no_sale_por_el_log_de_progreso(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """La línea que trae las claves se captura, pero NO se reemite.

    El resto de pasos vuelcan la salida del subproceso en las líneas de progreso
    (`_run` pasa `on_line=lines.append`), y esas líneas las imprime el CLI y las
    difunde el wizard por SSE. Con el one-shot eso dejaría las cinco unseal keys,
    el root token y la contraseña de admin en el log de la instalación — y el
    revelado de una sola vez se convierte en un revelado permanente escrito
    donde nadie lo va a borrar.
    """

    ex, _runner, *_ = _executor(
        installer_config,
        gen_secrets,
        runner=_bootstrap_runner(before=("Creating agentic-platform-bootstrap-run ... done",)),
    )

    lines = ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    blob = "\n".join(lines)

    for secret in (_ROOT_TOKEN, _ADMIN_PASSWORD, *_UNSEAL_KEYS):
        assert secret not in blob, f"secreto en el log de progreso: {secret}"
    assert BOOTSTRAP_REVEAL_EVENT not in blob
    # …pero lo que NO es secreto sí llega: si el paso enmudeciera del todo, un
    # fallo del one-shot sería indistinguible de un éxito.
    assert any("bootstrap-run" in line for line in lines), lines


def test_un_one_shot_que_falla_sale_como_mensaje_y_no_como_traza(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """rc≠0 del one-shot → `StepExecutionError` con el comando y la salida dentro."""

    runner = _bootstrap_runner(
        rc=2,
        before=("Error response from daemon: no such image",),
        reveal=False,
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)

    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    message = str(exc.value)
    assert BOOTSTRAP_SERVICE in message
    assert "rc=2" in message
    assert "no such image" in message, "sin la salida del one-shot no hay diagnóstico"


def test_la_mitad_que_falta_del_paso_8_se_nombra_en_el_error(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Si la imagen no trae el módulo, el error dice CUÁL y de qué es la mitad.

    Es la diferencia entre un diagnóstico y una sospecha sobre el Docker del
    operador — la misma regla que sigue el banner de `generate`.
    """

    runner = _bootstrap_runner(
        rc=1,
        before=(f"/usr/bin/python: No module named {BOOTSTRAP_ENTRYPOINT}",),
        reveal=False,
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)

    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    message = str(exc.value)
    assert BOOTSTRAP_ENTRYPOINT in message
    assert "ADR 0161" in message


def test_un_one_shot_en_verde_sin_revelado_no_se_da_por_bueno(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """rc=0 sin línea de revelado es un fallo, no un éxito silencioso.

    Sería el peor modo de fallo posible: la instalación sigue hasta el final,
    imprime que ha terminado, se autodestruye… y no hay credenciales que revelar
    porque nunca las hubo. El paso tiene que morir aquí, con el stack todavía
    entero y el operador delante.
    """

    ex, *_ = _executor(installer_config, gen_secrets, runner=_bootstrap_runner(reveal=False))

    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert BOOTSTRAP_REVEAL_EVENT in str(exc.value)


def test_las_unseal_keys_que_aporta_el_operador_viajan_por_entorno(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """`--vault-unseal-keys-from` sigue funcionando: las claves llegan al one-shot.

    Antes las usaba el cliente hvac del host. Ahora quien desella es el one-shot,
    así que si no viajaran, el reintento sobre un Vault ya inicializado y sellado
    volvería a morir — y la única salida documentada era destruir la instalación.

    Van por entorno y NUNCA por argv: un share en la línea de comandos queda a la
    vista de cualquier usuario del host en `ps` y en el historial del shell (es
    la misma razón por la que se leen de un fichero y no de un flag).
    """

    keys = ("share-aportado-1", "share-aportado-2", "share-aportado-3")
    runner = _bootstrap_runner(
        argv=_bootstrap_argv("-e", BOOTSTRAP_UNSEAL_KEYS_ENV),
        already_initialized=True,
        unseal_keys=[],
        root_token="",
    )
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=runner,
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        cfg=installer_config,
        secrets=gen_secrets,
        existing_unseal_keys=keys,
    )

    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert runner.envs[-1] == {
        BOOTSTRAP_UNSEAL_KEYS_ENV: BOOTSTRAP_UNSEAL_KEYS_SEPARATOR.join(keys)
    }
    argv = runner.calls[-1]
    assert BOOTSTRAP_UNSEAL_KEYS_ENV in argv, "el flag `-e` de paso a través"
    for key in keys:
        assert key not in argv, "un share en argv es un share en `ps`"


def test_un_vault_ya_inicializado_no_inventa_claves_que_revelar(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Re-bootstrap: el one-shot no re-inicializa, así que no hay init que revelar.

    Es el mismo tres-estados de antes (`init=None` + `already_initialized`), y lo
    que lo consume —`RealCredentialBuilder`— no cambia: falla ruidosamente en vez
    de revelar nada.
    """

    ex, *_ = _executor(
        installer_config,
        gen_secrets,
        runner=_bootstrap_runner(already_initialized=True, unseal_keys=[], root_token=""),
    )

    lines = ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert ex.vault_bootstrap_result is not None
    assert ex.vault_bootstrap_result.already_initialized is True
    assert ex.vault_bootstrap_result.init is None
    assert any("ya inicializado" in line for line in lines), lines


def test_la_siembra_no_se_repite_porque_el_one_shot_ya_sembro(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """SEED_TENANT rinde cuentas de lo que hizo el one-shot; no vuelve a sembrar.

    El one-shot hace las tres cosas (init de Vault, siembra y revelado), así que
    volver a lanzar `init_tenant` desde aquí no sería redundante y benigno: sería
    un DEFECTO. `init_tenant` es idempotente y NO cambia la contraseña de un
    usuario que ya existe, de modo que la segunda pasada mintearía una contraseña
    que la base de datos no ha visto nunca y el paso la marcaría como «el admin
    ya existía» — justo el aviso que dice que la contraseña revelada no sirve.
    """

    ex, runner, _writer, _tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    lines = ex.execute(InstallStep.SEED_TENANT, {})

    assert runner.calls == [_BOOTSTRAP_ARGV], "la siembra no vuelve a ejecutar nada"
    assert ex.seeded_admin_password == _ADMIN_PASSWORD
    assert ex.seeded_admin_user_existed is False
    assert ex.admin_password_advisories() == ()
    blob = "\n".join(lines)
    assert _ADMIN_PASSWORD not in blob
    assert BOOTSTRAP_SERVICE in blob, "hay que decir QUIÉN sembró, o parece que nadie"


def test_la_siembra_sin_revelado_del_one_shot_falla_en_vez_de_pasar_de_largo(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Sin one-shot detrás no hay tenant, y decir que sí lo hay es el peor final."""

    ex, *_ = _executor(installer_config, gen_secrets)

    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.SEED_TENANT, {})

    assert BOOTSTRAP_SERVICE in str(exc.value)


def test_no_step_returns_a_log_line_containing_a_secret(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    ex, *_ = _executor(installer_config, gen_secrets)
    lines: list[str] = []
    lines += ex.execute(InstallStep.GENERATE_CONFIG, {})
    lines += ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    lines += ex.execute(InstallStep.SEED_TENANT, {})
    blob = "\n".join(lines)
    assert _ROOT_TOKEN not in blob
    for key in _UNSEAL_KEYS:
        assert key not in blob
    assert (ex.seeded_admin_password or "x") not in blob


# ---------------------------------------------------------------------------
# Errores del sistema de ficheros: un mensaje, no una traza
# ---------------------------------------------------------------------------
@dataclass
class ExplodingEnvFileWriter:
    """Un escritor que falla como falla el sistema de ficheros de verdad.

    ``errno`` es el parámetro que importa: lo que el operador tiene que hacer
    con un EACCES (ejecutar con privilegios) no se parece en nada a lo que tiene
    que hacer con un ENOSPC (liberar disco) ni con un EROFS (montó la raíz de
    datos en solo lectura, el caso más probable del camino en contenedor).
    """

    errno_value: int
    message: str = "boom"
    written: dict[str, str] = field(default_factory=dict)
    modes: dict[str, int] = field(default_factory=dict)

    def write(self, path: str, content: str, *, mode: int) -> None:
        raise OSError(self.errno_value, self.message, path)


@pytest.mark.parametrize(
    ("errno_value", "expected"),
    [
        (errno.EACCES, "permiso"),
        (errno.EPERM, "permiso"),
        (errno.ENOSPC, "disco"),
        (errno.EROFS, "solo lectura"),
        (errno.ENOTDIR, "no es un directorio"),
    ],
)
def test_a_filesystem_failure_becomes_a_message_and_not_a_traceback(
    installer_config: InstallerConfig,
    gen_secrets: GeneratedSecrets,
    errno_value: int,
    expected: str,
) -> None:
    """EACCES/ENOSPC/EROFS/ENOTDIR salen como `StepExecutionError` explicado.

    Antes salían como veinte líneas de traceback terminadas en
    `PermissionError: [Errno 13] Permission denied: '/data/agent-platform'`, con
    la pila interna de pathlib, sin ningún «error:», sin código de salida de la
    tabla documentada (el proceso moría con 1, que en esa tabla significa
    «argumentos mal») y sin ninguna indicación de qué hacer. Es el fallo MÁS
    común del instalador: ejecutarlo sin permisos sobre la raíz de datos.
    """

    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=FakeCommandRunner(),
        env_writer=ExplodingEnvFileWriter(errno_value=errno_value),
        tree=FakeDataTreeProvisioner(),
        cfg=installer_config,
        secrets=gen_secrets,
    )

    with pytest.raises(StepExecutionError) as excinfo:
        ex.execute(InstallStep.GENERATE_CONFIG, {})

    message = str(excinfo.value)
    assert expected in message.lower(), message
    # La ruta concreta, o el operador no sabe dónde mirar.
    assert _COMPOSE_DIR in message


def test_a_failure_creating_the_data_tree_is_translated_too(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El `mkdir` del árbol de datos es la otra mitad, y fallaba igual de crudo.

    Es además la que más se rompe en la práctica: los `write` van a ficheros
    dentro de directorios que el propio escritor crea, pero el árbol incluye
    rutas con modos concretos, y ahí es donde revienta un `/data` de otro dueño.
    """

    class ExplodingTree:
        def provision(self, plan: list[object]) -> None:
            raise OSError(errno.EACCES, "Permission denied", f"{_COMPOSE_DIR}/postgres")

    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=FakeCommandRunner(),
        env_writer=FakeEnvFileWriter(),
        tree=ExplodingTree(),  # type: ignore[arg-type]
        cfg=installer_config,
        secrets=gen_secrets,
    )

    with pytest.raises(StepExecutionError) as excinfo:
        ex.execute(InstallStep.GENERATE_CONFIG, {})

    assert "permiso" in str(excinfo.value).lower()
    assert f"{_COMPOSE_DIR}/postgres" in str(excinfo.value)


# ---------------------------------------------------------------------------
# tls_mode: provided — dos rutas que el operador rellena y que nadie leía
# ---------------------------------------------------------------------------
def _provided_tls(cfg: InstallerConfig, *, cert: str, key: str) -> InstallerConfig:
    return cfg.model_copy(
        update={
            "system": cfg.system.model_copy(
                update={"tls_mode": "provided", "tls_cert_path": cert, "tls_key_path": key}
            )
        }
    )


def test_the_corporate_cert_the_operator_configured_is_actually_copied(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """`tls_cert_path`/`tls_key_path` se copian al bind que monta Caddy.

    La validación EXIGE esos dos campos (sin ellos rechaza el install.yaml), lo
    que refuerza la impresión de que sirven para algo; y hasta el 2026-08-27 no
    los leía ni una línea del repositorio. El instalador creaba
    `{data_root}/caddy/tls` VACÍO a 0700, generaba un Caddyfile con
    `tls /etc/caddy/tls/server.crt …` y montaba ese directorio vacío: Caddy no
    encontraba el certificado, nunca pasaba a healthy, `up -d --wait` fallaba y
    con él la instalación entera. Y como Caddy es el único servicio publicado, no
    quedaba ni por dónde mirar desde el navegador.
    """

    cfg = _provided_tls(installer_config, cert="/etc/ssl/agentic.crt", key="/etc/ssl/agentic.key")
    reader = FakeFileReader(
        files={
            # Sin cabecera PEM a propósito: la línea de apertura de una clave
            # privada, escrita en un fichero del repositorio, dispara el hook
            # `detect-private-key` — y con razón, porque no se distingue de una
            # de verdad. El test no necesita PEM: sólo necesita saber QUÉ
            # contenido acabó en qué ruta y con qué modo.
            "/etc/ssl/agentic.crt": "cert-corporativo-de-prueba\n",
            "/etc/ssl/agentic.key": "clave-privada-de-prueba\n",
        }
    )
    writer = FakeEnvFileWriter()
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=FakeCommandRunner(),
        env_writer=writer,
        tree=FakeDataTreeProvisioner(),
        cfg=cfg,
        secrets=gen_secrets,
        file_reader=reader,
    )

    ex.execute(InstallStep.GENERATE_CONFIG, {})

    assert "cert-corporativo-de-prueba" in writer.written[f"{_COMPOSE_DIR}/caddy/tls/server.crt"]
    assert "clave-privada-de-prueba" in writer.written[f"{_COMPOSE_DIR}/caddy/tls/server.key"]
    # La clave privada NO puede quedar con el modo del certificado.
    assert writer.modes[f"{_COMPOSE_DIR}/caddy/tls/server.crt"] == 0o644
    assert writer.modes[f"{_COMPOSE_DIR}/caddy/tls/server.key"] == 0o600


def test_a_cert_path_that_does_not_exist_fails_now_and_not_in_the_up(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Falla en el paso 1 con las dos rutas en el mensaje, no en el `up`.

    El operador no tiene motivo para sospechar del certificado: lo configuró él.
    Un fallo tardío le enseña `caddy unhealthy`, que apunta a cualquier otra
    cosa; uno temprano le enseña la ruta que ha escrito mal.
    """

    cfg = _provided_tls(installer_config, cert="/etc/ssl/no-esta.crt", key="/etc/ssl/no-esta.key")
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=FakeCommandRunner(),
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        cfg=cfg,
        secrets=gen_secrets,
        file_reader=FakeFileReader(files={}),
    )

    with pytest.raises(StepExecutionError) as excinfo:
        ex.execute(InstallStep.GENERATE_CONFIG, {})

    message = str(excinfo.value)
    assert "/etc/ssl/no-esta.crt" in message
    assert f"{_COMPOSE_DIR}/caddy/tls" in message, (
        "el mensaje tiene que dar la salida del camino en contenedor: dejar el "
        "par en el bind, donde SÍ es alcanzable"
    )


def test_a_cert_already_dropped_in_the_bind_is_accepted(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El camino `generate` dentro del contenedor: las rutas del host no existen.

    Ahí el par no se puede copiar —el contenedor sólo tiene montada la raíz de
    datos— así que la única forma correcta es comprobar que ya está en su sitio.
    Abortar aquí dejaría el camino sin clon sin ninguna forma de usar un
    certificado corporativo.
    """

    cfg = _provided_tls(installer_config, cert="/host/agentic.crt", key="/host/agentic.key")
    reader = FakeFileReader(
        files={
            f"{_COMPOSE_DIR}/caddy/tls/server.crt": "ya estaba",
            f"{_COMPOSE_DIR}/caddy/tls/server.key": "ya estaba",
        }
    )
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=FakeCommandRunner(),
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        cfg=cfg,
        secrets=gen_secrets,
        file_reader=reader,
    )

    lines = ex.execute(InstallStep.GENERATE_CONFIG, {})

    assert any("caddy/tls" in line for line in lines), lines


def test_only_the_provided_mode_touches_the_tls_bind(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Con `internal` (el default) no se escribe ningún certificado.

    Control negativo: sin él, un arreglo que escribiera siempre pasaría el test
    de arriba y rompería el modo por defecto, que es el que usa casi todo el
    mundo.
    """

    ex, _runner, writer, _tree = _executor(installer_config, gen_secrets)
    ex.execute(InstallStep.GENERATE_CONFIG, {})

    assert not [p for p in writer.written if "/caddy/tls/" in p]


# ---------------------------------------------------------------------------
# La contraseña que se revela tiene que ser la que abre la cuenta
# ---------------------------------------------------------------------------
def _seed_runner(*, created_user: bool | None) -> FakeCommandRunner:
    """Un one-shot que dice —o calla— si el usuario admin se creó en esta pasada.

    Tres estados, y los tres importan. `created_user=None` guioniza el caso en
    que el one-shot NO lo declara en su revelado: el instalador cae entonces al
    marcador `init_tenant.completed`, que `init_tenant` emite por su cuenta
    dentro del mismo contenedor. Y si tampoco está —formato de log cambiado,
    nivel subido—, la respuesta es «no lo sé», que no es lo mismo que «salió
    bien» y el revelado la trata distinto.
    """

    if created_user is None:
        return _bootstrap_runner(admin_user_created=None)
    return _bootstrap_runner(admin_user_created=created_user)


def test_a_freshly_created_admin_reveals_the_password_that_was_minted(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Instalación limpia: la contraseña minteada ES la que abre la cuenta."""

    ex, *_ = _executor(installer_config, gen_secrets, runner=_seed_runner(created_user=True))

    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    ex.execute(InstallStep.SEED_TENANT, {})

    assert ex.seeded_admin_password == _ADMIN_PASSWORD
    assert ex.seeded_admin_user_existed is False
    assert ex.admin_password_advisories() == ()


def test_an_admin_that_already_existed_is_not_given_a_password_it_does_not_have(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """`init_tenant` es idempotente: NO cambia la contraseña de un usuario que ya está.

    Su docstring lo dice por escrito («the password of an existing user is left
    untouched»). El instalador, en cambio, mintea una nueva en CADA ejecución y
    se la enseñaba al operador como «Contraseña del administrador». En cualquier
    reintento sobre datos conservados eso es una contraseña que la base de datos
    no ha visto nunca: el operador la guarda, el instalador se autodestruye, y en
    el primer login recibe credenciales inválidas sin ninguna pista de por qué.

    Quien mintea ahora es el one-shot, pero la trampa es la MISMA y por eso el
    test sigue aquí: lo que cambió es de dónde llega el dato, no que el dato deje
    de hacer falta.
    """

    ex, *_ = _executor(installer_config, gen_secrets, runner=_seed_runner(created_user=False))

    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    ex.execute(InstallStep.SEED_TENANT, {})

    assert ex.seeded_admin_user_existed is True
    advisories = ex.admin_password_advisories()
    assert advisories, "el revelado no puede callarse esto"
    blob = " ".join(advisories).lower()
    assert "ya exist" in blob
    assert "no la ha cambiado" in blob
    # Y la contraseña minteada NO puede aparecer en el aviso.
    assert (ex.seeded_admin_password or "x") not in " ".join(advisories)


def test_el_marcador_de_init_tenant_sirve_de_respaldo_si_el_revelado_calla(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Sin `admin_user_created` en el revelado, vale el log del propio `init_tenant`.

    El one-shot ejecuta `api_server.seeds.init_tenant` dentro de su contenedor, y
    ése emite `init_tenant.completed` con `created_user` por su cuenta. Leerlo
    como respaldo no es acoplarse dos veces al mismo dato: es no perder la
    respuesta cuando la mitad que aún no existe se implemente sin ese campo.
    """

    runner = _bootstrap_runner(
        admin_user_created=None,
        before=(
            '{"event": "init_tenant.completed", "tenant_id": "0192", '
            '"user_id": "0193", "created_org": false, "created_user": false, '
            '"created_membership": false, "level": "info"}',
        ),
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)

    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert ex.seeded_admin_user_existed is True


def test_an_unreadable_seed_result_is_reported_as_unknown_not_as_success(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Si no consta por ninguna vía, se dice «no se ha podido confirmar».

    Es la diferencia entre acoplarse a un formato de log y depender de él en
    silencio: el día que `init_tenant` cambie su salida, el instalador tiene que
    avisar de que ya no sabe, no seguir afirmando que la contraseña es buena.
    """

    ex, *_ = _executor(installer_config, gen_secrets, runner=_seed_runner(created_user=None))

    ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    ex.execute(InstallStep.SEED_TENANT, {})

    assert ex.seeded_admin_user_existed is None
    assert any("no se ha podido" in a.lower() for a in ex.admin_password_advisories())


# ---------------------------------------------------------------------------
# El depósito de emergencia de las unseal keys
# ---------------------------------------------------------------------------
def _escrowed_executor(
    cfg: InstallerConfig,
    secrets: GeneratedSecrets,
    runner: FakeCommandRunner,
) -> tuple[RealStepExecutor, FileKeyEscrow, FakeEscrowFile]:
    store = FakeEscrowFile()
    escrow = FileKeyEscrow(data_root=_COMPOSE_DIR, store=store)
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=runner,
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        cfg=cfg,
        secrets=secrets,
        key_escrow=escrow,
    )
    return ex, escrow, store


def test_the_unseal_keys_are_deposited_the_moment_vault_is_initialised(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Antes de seguir al paso siguiente, las claves ya están en disco.

    Lo que cambió con la delegación es de DÓNDE salen: antes las devolvía el
    cliente hvac del host, ahora se leen de la línea de revelado del one-shot. Lo
    que NO cambia —y es lo que este test fija— es que se depositan lo primero:
    entre el init y el revelado hay minutos, y si el proceso muere en ese tramo
    esas cinco claves no vuelven a existir por ningún camino.
    """

    ex, escrow, store = _escrowed_executor(installer_config, gen_secrets, _bootstrap_runner())

    lines = ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert escrow.pending_path() == f"{_COMPOSE_DIR}/{UNSEAL_KEYS_FILENAME}"
    assert store.modes[escrow.path] == 0o600
    # Y lo depositado son las claves DEL ONE-SHOT, no un placeholder.
    deposited = store.files[escrow.path]
    for key in _UNSEAL_KEYS:
        assert key in deposited
    assert _ROOT_TOKEN in deposited
    # El operador tiene que ver que existe, o no sabrá que hay que borrarlo.
    assert any(UNSEAL_KEYS_FILENAME in line for line in lines), lines
    # …pero la línea de log NO lleva ninguna clave dentro.
    assert _ROOT_TOKEN not in "\n".join(lines)


class _UnwritableEscrowFile:
    """Un depósito sobre un sistema de ficheros que no admite escrituras."""

    def write(self, path: str, content: str, *, mode: int) -> None:
        raise PermissionError(errno.EACCES, "Permission denied", path)

    def exists(self, path: str) -> bool:
        return False

    def remove(self, path: str) -> None:  # pragma: no cover - nunca se llega
        raise AssertionError("no hay nada que borrar")


def test_un_deposito_que_no_se_puede_escribir_avisa_pero_no_tumba_la_instalacion(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """La red de seguridad puede fallar; abortar por ella garantizaría la caída.

    Si la raíz de datos no admite escrituras, el depósito no se puede escribir.
    Abortar ahí sería lo contrario de lo que el depósito persigue: las claves
    siguen vivas en memoria y el revelado del final las va a enseñar, así que
    tumbar la instalación en ese punto convierte «me he quedado sin red» en «he
    perdido las claves». Lo que no puede hacer es callárselo — ni salir como
    traza, que es como salía cualquier fallo del sistema de ficheros de este
    paso antes del 2026-08-27.
    """

    escrow = FileKeyEscrow(data_root=_COMPOSE_DIR, store=_UnwritableEscrowFile())
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=_bootstrap_runner(),
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        cfg=installer_config,
        secrets=gen_secrets,
        key_escrow=escrow,
    )

    lines = ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert ex.vault_bootstrap_result is not None, "el paso tiene que haber terminado"
    blob = "\n".join(lines)
    assert "AVISO" in blob
    assert "sin permiso" in blob, "hay que decir POR QUÉ no se pudo escribir"
    for key in _UNSEAL_KEYS:
        assert key not in blob


def test_claves_acunadas_sin_deposito_y_one_shot_muerto_se_dice_que_es_irreversible(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Los dos fallos a la vez: el peor estado posible, y el que no se puede callar.

    Vault se inicializó, las claves no se pudieron escribir en ninguna parte y el
    one-shot murió después. Ese Vault queda sellado sin recuperación, y reintentar
    la instalación va a fallar exactamente igual para siempre. Decir sólo «el
    one-shot falló» manda al operador a un bucle: reintenta, vuelve a fallar, y
    nada en pantalla explica por qué esta vez tampoco.
    """

    escrow = FileKeyEscrow(data_root=_COMPOSE_DIR, store=_UnwritableEscrowFile())
    ex = RealStepExecutor(
        compose_dir=_COMPOSE_DIR,
        runner=_bootstrap_runner(rc=1, before=("seeding builtins…",)),
        env_writer=FakeEnvFileWriter(),
        tree=FakeDataTreeProvisioner(),
        cfg=installer_config,
        secrets=gen_secrets,
        key_escrow=escrow,
    )

    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    message = str(exc.value)
    assert "sin recuperaci" in message, "hay que decir que es irreversible"
    assert "04-disaster-recovery" in message, "y adónde va a mirar el operador"
    for key in _UNSEAL_KEYS:
        assert key not in message


def test_si_el_one_shot_muere_tras_inicializar_vault_las_claves_ya_estan_a_salvo(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El peor caso del paso: Vault inicializado y el one-shot muerto después.

    Es exactamente el hueco para el que existe el depósito, sólo que ahora cabe
    ENTERO dentro de un único subproceso: el one-shot inicializa Vault, emite el
    revelado, se pone a sembrar el catálogo built-in —minutos— y se muere. Si el
    depósito esperase a que `rc == 0`, las cinco claves se irían con el proceso y
    ese Vault no se podría desellar nunca más.
    """

    runner = _bootstrap_runner(rc=1, before=("seeding builtins…",))
    # El revelado sale ANTES del fallo, que es como ocurre de verdad.
    ex, escrow, _store = _escrowed_executor(installer_config, gen_secrets, runner)

    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.BOOTSTRAP_VAULT, {})

    assert escrow.pending_path() is not None, "las claves se han perdido"
    message = str(exc.value)
    assert UNSEAL_KEYS_FILENAME in message, (
        "el error tiene que decir dónde quedaron las claves: sin eso el operador "
        "no sabe que están a un `cat` de distancia"
    )
    for key in _UNSEAL_KEYS:
        assert key not in message, "el mensaje de error no es un canal de revelado"


# ---------------------------------------------------------------------------
# El mensaje de un comando que falla lleva su salida (2026-08-28)
# ---------------------------------------------------------------------------
#
# Lo destapó la tercera ejecución del e2e de instalación (run 33169724473). El
# install murió en `start_stack` y lo único que dijo fue:
#
#     el comando falló (rc=1): docker compose -p agentic-platform … up -d --wait
#
# Qué servicio no arrancó, y por qué, se lo quedó el instalador — aunque el
# runner lo tenía capturado en `output_lines` desde el principio: `_run` lo
# tiraba al construir el error.
#
# El coste no es cosmético. Sin la salida, diagnosticar obliga a reproducir a
# mano lo que la máquina acaba de ver; en casa de un cliente, eso es una llamada
# de soporte por cada fallo.


def _resultado_fallido(lineas: tuple[str, ...]) -> CommandResult:
    return CommandResult(returncode=1, output_lines=lineas)


def test_el_error_de_un_comando_lleva_su_salida() -> None:
    """Sin esto, «falló» es todo lo que el operador sabe."""
    salida = RealStepExecutor._cola_del_fallo(
        _resultado_fallido(
            (
                "dependency failed to start: container agentic-platform-vault-1 is unhealthy",
                "",
            )
        )
    )
    assert "vault-1 is unhealthy" in salida, f"la causa real no aparece en el mensaje: {salida!r}"


def test_una_salida_larga_se_recorta_por_el_final() -> None:
    """El final es donde está la causa; la cabecera avisa de que se recortó.

    Un `docker compose up` escupe cientos de líneas de progreso de descarga. Si
    el mensaje las llevara todas, la causa quedaría enterrada — que es otra
    forma de no decirla.
    """
    muchas = tuple(f"linea {i}" for i in range(200))
    salida = RealStepExecutor._cola_del_fallo(_resultado_fallido(muchas))
    assert "linea 199" in salida, "se ha recortado por el lado equivocado"
    assert "linea 0" not in salida, "no se ha recortado"
    assert "de 200" in salida, "no avisa de que hay más líneas de las que enseña"


def test_un_comando_mudo_lo_dice_en_vez_de_callar() -> None:
    """Un mensaje vacío se lee como «no hay información», y no es lo mismo.

    «No escribió nada» es un dato: descarta que la causa esté en la salida y
    manda a mirar el código de salida y el entorno. Un hueco en blanco sólo
    hace dudar de si el instalador la perdió.
    """
    salida = RealStepExecutor._cola_del_fallo(_resultado_fallido(("", "   ")))
    assert "no escribió nada" in salida


# ---------------------------------------------------------------------------
# Cuando `up --wait` falla, el error trae los LOGS (2026-08-28)
# ---------------------------------------------------------------------------
#
# `docker compose up --wait` informa del ESTADO de cada contenedor y de nada
# mas. Medido en el e2e (run 33170713059): dos servicios en `Error` -postgres y
# docker-socket-proxy- y CERO lineas de sus logs en las 12.954 del job. El
# mensaje nombraba al culpable sin decir que le pasaba.
#
# No basta con confiar en que quien ejecute tenga un paso de diagnostico: el
# operador de un cliente no lo tiene. El instalador los recoge el.


def _argv_up() -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "-p",
        PROJECT_NAME,
        "-f",
        f"{_COMPOSE_DIR}/docker-compose.yml",
        "up",
        "-d",
        "--wait",
    )


def _argv_logs(servicio: str) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "-p",
        PROJECT_NAME,
        "-f",
        f"{_COMPOSE_DIR}/docker-compose.yml",
        "logs",
        "--no-color",
        "--tail=40",
        servicio,
    )


def test_el_fallo_de_up_trae_los_logs_del_servicio_que_no_arranco(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El caso real: postgres en `Error` y su log dentro del mensaje."""
    runner = FakeCommandRunner(
        responses={
            _argv_up(): CommandResult(
                returncode=1,
                output_lines=(" Container agentic-platform-postgres-1  Error",),
            ),
            _argv_logs("postgres"): CommandResult(
                returncode=0,
                output_lines=("postgres-1 | FATAL: la contrasena no coincide",),
            ),
        }
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.START_STACK, {})
    mensaje = str(exc.value)
    assert "postgres" in mensaje, "no nombra el servicio que fallo"
    assert "FATAL" in mensaje, f"nombra al culpable pero no dice que le pasa: {mensaje!r}"


def test_si_compose_no_nombra_a_nadie_se_miran_los_cimientos(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """El caso que deja al operador sin nada: compose falla sin senalar.

    Si postgres no arranca, los veinte servicios que lo esperan por
    `depends_on: healthy` se quedan en `Created` y compose puede no nombrar a
    ninguno. Mirar los cuatro cimientos es la apuesta correcta: el error real
    esta ahi o no esta en ninguna parte.
    """
    runner = FakeCommandRunner(
        responses={_argv_up(): CommandResult(returncode=1, output_lines=("algo salio mal",))}
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.START_STACK, {})
    mensaje = str(exc.value)
    for cimiento in ("postgres", "redis", "vault", "minio"):
        assert cimiento in mensaje, f"no se miro {cimiento}"


def test_un_servicio_sin_log_lo_dice_en_vez_de_dejar_un_hueco(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """«Sin salida» es un dato; un blanco solo hace dudar de si se perdio."""
    runner = FakeCommandRunner(
        responses={
            _argv_up(): CommandResult(
                returncode=1,
                output_lines=(" Container agentic-platform-vault-1  Error",),
            ),
            _argv_logs("vault"): CommandResult(returncode=0, output_lines=()),
        }
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.START_STACK, {})
    assert "sin salida" in str(exc.value)


def test_un_one_shot_que_sale_distinto_de_cero_tambien_se_recoge(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """La SEGUNDA forma de fallar de compose, que la primera versión perdía.

    Un servicio de larga vida cuyo healthcheck no llega sale como `… Error`. Un
    one-shot que sale distinto de cero —`migrations`, `bootstrap`— sale así:

        Container agentic-platform-migrations-1  service "migrations"
          didn't complete successfully: exit 1

    …que NO termina en `Error`. Medido en el e2e run 33180241225: el install
    murió por `migrations`, y el mensaje enseñó los logs de los cuatro cimientos
    —los tres sanos— y ni una línea del servicio que había fallado.

    Un recolector que mira sólo una de las dos formas es peor que ninguno: no
    calla, enseña lo que no toca, y manda a diagnosticar el sitio equivocado.
    """
    linea = (
        ' Container agentic-platform-migrations-1  service "migrations" '
        "didn't complete successfully: exit 1"
    )
    runner = FakeCommandRunner(
        responses={
            _argv_up(): CommandResult(returncode=1, output_lines=(linea,)),
            _argv_logs("migrations"): CommandResult(
                returncode=0,
                output_lines=("migrations-1 | alembic: FAILED: no such revision",),
            ),
        }
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.START_STACK, {})
    mensaje = str(exc.value)
    assert "migrations" in mensaje, "no nombra el one-shot que falló"
    assert "no such revision" in mensaje, f"no trae el log del one-shot: {mensaje!r}"
    assert "postgres" not in mensaje, (
        "ha caído a los cimientos habiendo un culpable nombrado: enseñaría los "
        "logs de tres servicios sanos y escondería el que importa"
    )


@pytest.mark.parametrize(
    ("linea", "servicio"),
    [
        # Forma 1 — larga vida cuyo healthcheck no llegó (run 33171640034).
        (" Container agentic-platform-postgres-1  Error", "postgres"),
        # Forma 2 — one-shot que sale != 0 (run 33180241225).
        (
            ' Container agentic-platform-migrations-1  service "migrations" '
            "didn't complete successfully: exit 1",
            "migrations",
        ),
        # Forma 3 — en MINÚSCULA y sin tabular (run 33182384445).
        (" container agentic-platform-cortex-beat-1 is unhealthy", "cortex-beat"),
        # Los dos proxies no llevan el prefijo del proyecto.
        (" Container agentic-egress-proxy  Error", "egress-proxy"),
    ],
)
def test_las_tres_formas_en_que_compose_dice_que_algo_fallo(
    installer_config: InstallerConfig,
    gen_secrets: GeneratedSecrets,
    linea: str,
    servicio: str,
) -> None:
    """Cada una costó una ejecución del e2e descubrirla.

    `docker compose up --wait` no tiene UNA manera de reportar un fallo: tiene
    tres, y la tercera llega en minúscula y sin el formato tabulado de las otras
    dos porque la emite otra parte de su código.

    Están juntas en un solo test a propósito. Parcheándolas de una en una —que
    es lo que hice tres veces— cada formato nuevo cuesta una ejecución entera y
    el recolector cae al fallback, enseñando los logs de servicios sanos: no
    calla, apunta al sitio equivocado.
    """
    runner = FakeCommandRunner(
        responses={
            _argv_up(): CommandResult(returncode=1, output_lines=(linea,)),
            _argv_logs(servicio): CommandResult(
                returncode=0, output_lines=(f"{servicio}-1 | la pista que importa",)
            ),
        }
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.START_STACK, {})
    mensaje = str(exc.value)
    assert servicio in mensaje, f"no identificó `{servicio}` en: {linea!r}"
    assert "la pista que importa" in mensaje, f"identificó `{servicio}` pero no trajo su log"


def test_el_fallo_del_one_shot_prioriza_las_lineas_de_error(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Una cola corta no vale cuando el one-shot es charlatán.

    La siembra del catálogo imprime una línea por elemento indexado. Con una
    ventana de ocho líneas, el error que causó el fallo se queda fuera y el
    mensaje enseña ruido de progreso — medido en el e2e run 33193255711, donde
    el one-shot salió con rc=5 (DATABASE) y las ocho últimas líneas eran todas
    `catalog_ingestion.indexed`.

    Que el mensaje exista no basta: tiene que llevar LA línea que importa.
    """
    ruido = [
        f'{{"slug": "cosa-{i}", "event": "catalog_ingestion.indexed", "level": "info"}}'
        for i in range(30)
    ]
    error = '{"event": "seed.failed", "level": "error", "detail": "relation does not exist"}'
    runner = _bootstrap_runner(rc=5, before=(error, *ruido), reveal=False)
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    mensaje = str(exc.value)
    assert "relation does not exist" in mensaje, (
        "el mensaje no trae la línea de error: quedó enterrada bajo el ruido de "
        f"progreso.\n{mensaje[:400]}"
    )


def test_un_aviso_con_campo_error_no_se_confunde_con_la_causa(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """`"error":` en una línea NO la convierte en el error (run 33194504572).

    El aviso de ollama es un WARNING que lleva un campo `error`. Con un filtro
    por subcadena, seis copias suyas llenaban la ventana y el fallo real seguía
    sin verse — el mensaje pasó de enseñar ruido de progreso a enseñar ruido de
    avisos, que no es mejor.

    El NIVEL es lo que el emisor declara sobre la gravedad; el campo `error` es
    sólo un dato suyo.
    """
    aviso = '{"error": "ollama embed failed", "event": "embedder_failed", "level": "warning"}'
    real = '{"event": "seed.failed", "level": "error", "detail": "column x does not exist"}'
    runner = _bootstrap_runner(rc=5, before=(real, *([aviso] * 20)), reveal=False)
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    mensaje = str(exc.value)
    assert "column x does not exist" in mensaje, (
        f"la causa real no aparece; la tapan los avisos:\n{mensaje[:500]}"
    )
    assert mensaje.count("ollama embed failed") <= 1, (
        "el mismo aviso aparece repetido: seis copias idénticas no informan"
    )


def test_si_muere_sin_registrar_nada_grave_se_ve_el_final(
    installer_config: InstallerConfig, gen_secrets: GeneratedSecrets
) -> None:
    """Una excepción no capturada no deja línea de nivel `error`.

    Por eso la cola se enseña SIEMPRE, no sólo cuando no hay severas: si el
    proceso murió de golpe, el final de su salida es lo único que queda.
    """
    runner = _bootstrap_runner(
        rc=5, before=("paso 1", "paso 2", "lo último que hizo"), reveal=False
    )
    ex, *_ = _executor(installer_config, gen_secrets, runner=runner)
    with pytest.raises(StepExecutionError) as exc:
        ex.execute(InstallStep.BOOTSTRAP_VAULT, {})
    assert "lo último que hizo" in str(exc.value)
