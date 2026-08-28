"""Real install StepExecutor — provisions the stack for real (Plan prod-01 task_16).

Implements the :class:`installer_backend.install.StepExecutor` Protocol with
injected seams (a :class:`CommandRunner` for ``docker compose``, the config
:class:`EnvFileWriter`/:class:`DataTreeProvisioner`, the key escrow), so the
orchestration is fully unit-testable without a Docker host. ``FakeStepExecutor``
stays for ``--dry-run`` and the existing suite.

Per step:
  * **GENERATE_CONFIG** — render + write ``docker-compose.yml`` (0640), ``.env``
    (0600, prod secret-guarded), ``config/global.yaml`` (0640) and the
    ``caddy/Caddyfile`` (0644, the compose bind-mounts it so it MUST exist before
    ``up``); copy out the shipped auxiliaries under ``stack/``
    (:mod:`installer_backend.stack_assets` — Postgres init scripts, Vault config,
    seccomp profiles, the two tinyproxy build contexts, monitoring config: every
    one of them a bind the generated compose declares, so a missing one is not a
    degraded service but a stack that does not come up); then provision the
    ``/data`` tree.
  * **PULL_IMAGES** — ``docker compose pull``.
  * **START_STACK** — ``docker compose up -d --wait`` (Compose blocks until every
    service is healthy or fails — no hand-rolled polling).
  * **RUN_MIGRATIONS** — ``docker compose run --rm migrations`` (the one-shot).
  * **BOOTSTRAP_VAULT** — ``docker compose run --rm bootstrap``: el one-shot de
    finalización del compose generado. Ver abajo.
  * **SEED_TENANT** — ya no siembra: rinde cuentas de lo que sembró el one-shot.

La finalización corre DENTRO de la red del stack, no desde el host
------------------------------------------------------------------
Hasta el 2026-08-28 el paso BOOTSTRAP_VAULT hablaba con Vault por HTTP **desde el
host**: ``real_bindings.build_hvac_vault_client`` construía un cliente ``hvac``
contra ``os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")``. Y el servicio
``vault`` del compose generado **no publica ningún puerto**: el único que publica
es Caddy (``80:80``/``443:443``, ADR 0061), y el Caddyfile generado sólo enruta
``api-server`` y ``admin-panel``. O sea que aunque las imágenes estuvieran
publicadas, **la instalación moría en el paso 4**, y como aquí sólo se capturaba
:class:`VaultBootstrapError`, el ``ConnectionRefusedError`` de ``requests`` salía
como traza cruda de Python con un exit code que la tabla del CLI llama
«argumentos mal».

El arreglo **no** es publicar el 8200 —sería ampliar la superficie publicada para
ahorrarse un rediseño, contra un ADR aceptado— sino el que ya decidió el
ADR 0161: «el bootstrap de Vault y la siembra corren **dentro de la red del
stack ya levantado**, que es donde tienen que correr». Eso existe: es el servicio
one-shot :data:`~installer_backend.compose_generator.BOOTSTRAP_SERVICE`, bajo
``profiles: [bootstrap]``, con la imagen del api-server y ``depends_on`` de Vault
+ postgres + migraciones.

Con esto **los dos caminos ejecutan lo mismo**. El ``generate`` sin clon deja al
operador ``docker compose run --rm bootstrap``; el ``install`` desde el host
ejecuta ese mismo comando por él. Antes eran dos implementaciones distintas de la
finalización, y sólo una de ellas —la que entonces no existía todavía— podía
funcionar. La otra mitad aterrizó el 2026-08-28 (``api_server.bootstrap``), así
que hoy los dos caminos llegan al final por la misma implementación.

El contrato con la otra mitad (:data:`BOOTSTRAP_REVEAL_EVENT`)
--------------------------------------------------------------
El one-shot hace las **tres** cosas: init+unseal de Vault, siembra del catálogo y
del tenant, y el revelado. Este ejecutor ya no hace ninguna; lo que hace es
**leer** lo que el one-shot cuenta, en UNA línea JSON de su stdout marcada con
:data:`BOOTSTRAP_REVEAL_EVENT`. Esa línea lleva material sin recuperación posible
(las cinco unseal keys, el root token, la contraseña de admin), así que:

* se **captura aparte** y NUNCA se vuelca en las líneas de progreso, que el CLI
  imprime y el wizard difunde por SSE (:func:`_redact_bootstrap_output`);
* lo que trae se **deposita en el escrow antes que nada** —incluso antes de mirar
  el código de salida—, porque el tramo caro (init hecho, revelado no impreso)
  ahora cabe entero dentro de un solo subproceso;
* un one-shot en verde **sin** esa línea es un fallo, no un éxito silencioso.

Lo que se fue con la siembra, y adónde
--------------------------------------
Este módulo tenía un ``_slugify`` con el cap de 64 caracteres de
``organizations.slug``, y existía por un motivo concreto: ``tenant_name`` admite
hasta 120, así que un nombre largo reventaba el INSERT **en el último paso de la
instalación**, que es el peor sitio donde descubrirlo. Al dejar de sembrar desde
aquí, el slug lo deriva el one-shot a partir del ``AGENTIC_BOOTSTRAP_TENANT_NAME``
que le pasa el compose, así que **el cap se debe allí**. Se anota porque un
guardarraíl que se borra sin decir de qué protegía vuelve como bug: el mismo
INSERT, el mismo paso, y nadie recordando por qué había un 64 escrito.

Los fallos del sistema de ficheros son mensajes, no trazas
----------------------------------------------------------
Hasta el 2026-08-27 este ejecutor sólo convertía en :class:`StepExecutionError`
los fallos de SUBPROCESO. Un ``PermissionError`` sobre la raíz de datos —el fallo
más común del instalador: ejecutarlo sin privilegios, o con la raíz montada en
solo lectura desde el contenedor— salía como veinte líneas de traceback con la
pila interna de ``pathlib``, sin ningún «error:», sin ningún código de salida de
la tabla documentada (el proceso moría con 1, que en esa tabla significa
«argumentos mal») y sin ninguna indicación de qué hacer. Ahora toda escritura y
el ``mkdir`` del árbol pasan por :meth:`RealStepExecutor._write` /
:func:`_describe_os_error`, que traducen por ``errno`` a una frase con la ruta y
la remediación.
"""

from __future__ import annotations

import errno as _errno
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from . import stack_assets
from .command_runner import CommandResult, CommandRunner
from .compose_generator import (
    BOOTSTRAP_ENTRYPOINT,
    BOOTSTRAP_SERVICE,
    PROJECT_NAME,
    STACK_ASSETS_DIR_NAME,
    generate_compose,
    render_compose_yaml,
)
from .config import Environment, InstallerConfig
from .config_generators import (
    DataTreeProvisioner,
    EnvFileWriter,
    GeneratedSecrets,
    assert_env_passes_prod_secret_guard,
    build_data_tree_plan,
    build_stack_dirs_plan,
    generate_env_file,
    generate_global_config,
    render_global_yaml,
)
from .install import InstallStep, StepExecutionError
from .install_state import FileReader
from .key_escrow import KeyEscrow
from .proxy_generator import generate_caddyfile
from .real_bindings import RealFileReader
from .vault_bootstrap import VaultBootstrapResult, VaultClient, VaultInitResult

#: El evento de la línea de REVELADO del one-shot de finalización, y con él el
#: contrato entre las dos mitades del paso 8 del ADR 0161.
#:
#: El one-shot (:data:`~installer_backend.compose_generator.BOOTSTRAP_ENTRYPOINT`,
#: que vive en la imagen del api-server) emite **una** línea de JSON con este
#: ``event`` y estos campos:
#:
#: ==========================  =========================================
#: ``already_initialized``     ``true`` si Vault YA estaba inicializado y
#:                             este one-shot NO lo re-inicializó.
#: ``unseal_keys``             los shares de Shamir. Vacío si lo anterior.
#: ``root_token``             el root token inicial. Vacío si lo anterior.
#: ``key_threshold``           cuántos shares hacen falta para desellar.
#: ``kv_mount``                dónde quedó montado el KV v2.
#: ``kv_enabled``              si este one-shot lo montó.
#: ``policies_written``        nombres de las políticas por servicio.
#: ``admin_password``          la contraseña CSPRNG del primer System Owner.
#: ``admin_user_created``      ``true`` si el usuario admin nació aquí;
#:                             ``false`` si ya existía. Ausente = no consta.
#: ==========================  =========================================
#:
#: **Por qué una línea de JSON y no un formato propio.** Ya hay precedente en
#: este mismo fichero: :data:`_SEED_MARKER_EVENT`. El logging del api-server
#: emite JSON de una línea por registro, así que el marcador sale gratis y se
#: parsea buscando las llaves DENTRO de la línea —no con ``json.loads`` de la
#: línea entera— para sobrevivir al prefijo que antepone Compose
#: (``bootstrap-1  | {...}``).
#:
#: **Y por qué esta línea no se reemite.** Lleva dentro material de una sola vez
#: y sin recuperación. El resto de pasos vuelcan la salida del subproceso en las
#: líneas de progreso, que el CLI imprime y el wizard difunde por SSE; hacer eso
#: aquí convertiría el revelado único en un revelado permanente escrito donde
#: nadie lo va a borrar. Ver :func:`_redact_bootstrap_output`.
BOOTSTRAP_REVEAL_EVENT = "bootstrap.reveal"

#: Por dónde viajan al one-shot las unseal keys que aporta el operador
#: (``--vault-unseal-keys-from``), para reintentar sobre un Vault YA inicializado
#: y SELLADO. Antes las usaba el cliente ``hvac`` del host; ahora quien desella
#: es el one-shot, así que si no viajaran, ese reintento volvería a morir y la
#: única salida documentada sería `uninstall --purge-data`.
#:
#: Viajan por ENTORNO y jamás por ``argv``: un share en la línea de comandos
#: queda a la vista de cualquier usuario del host en ``ps`` y en el historial del
#: shell. Es la misma razón por la que se leen de un fichero y no de un flag.
BOOTSTRAP_UNSEAL_KEYS_ENV = "AGENTIC_BOOTSTRAP_UNSEAL_KEYS"

#: Cómo se empaquetan varios shares en esa única variable. Una coma es inequívoca
#: sobre el alfabeto base64 de un share de Vault.
BOOTSTRAP_UNSEAL_KEYS_SEPARATOR = ","

#: La firma de «la imagen no trae el módulo». Se cruza con
#: :data:`BOOTSTRAP_ENTRYPOINT` en vez de fijar el texto entero porque el formato
#: cambia entre `runpy` y `ModuleNotFoundError` (con y sin comillas).
_MISSING_MODULE_HINT = "No module named"

#: Dónde monta Caddy el par certificado+clave corporativo (``tls_mode:
#: provided``). El nombre de los dos ficheros lo fija el Caddyfile generado
#: (``proxy_generator._PROVIDED_CERT`` / ``_PROVIDED_KEY``); aquí se escribe el
#: lado HOST del mismo bind, que es el que estaba quedándose vacío.
_TLS_BIND_SUBDIR = "caddy/tls"
_TLS_CERT_NAME = "server.crt"
_TLS_KEY_NAME = "server.key"

#: El evento que ``api_server.seeds.init_tenant`` emite al terminar, con
#: ``created_user`` dentro. Es un log estructurado en JSON (una línea por
#: registro), así que sirve de marcador legible por máquina sin inventar un
#: canal nuevo. Ver :func:`_admin_user_existed_from`.
_SEED_MARKER_EVENT = "init_tenant.completed"

#: Traducción por ``errno`` de los fallos del sistema de ficheros. La clave es
#: que lo que el operador tiene que HACER es distinto en cada caso: un EACCES se
#: arregla con privilegios o un `chown`, un ENOSPC liberando disco, y un EROFS
#: —el más probable del camino en contenedor— quitando el `:ro` del `-v`.
_OS_ERROR_REMEDIES: dict[int, str] = {
    _errno.EACCES: (
        "sin permiso de escritura en {path}: ejecuta el instalador con privilegios "
        "sobre la raíz de datos, o corrige su propietario (`chown`)"
    ),
    _errno.EPERM: (
        "sin permiso para operar sobre {path}: ejecuta el instalador con "
        "privilegios sobre la raíz de datos, o corrige su propietario (`chown`)"
    ),
    _errno.ENOSPC: "disco lleno al escribir {path}: libera espacio en el volumen de datos",
    _errno.EROFS: (
        "el sistema de ficheros de {path} está montado en SOLO LECTURA: si estás "
        "usando la imagen del instalador, monta la raíz de datos sin `:ro`"
    ),
    _errno.ENOTDIR: "{path}: algún elemento de la ruta existe y no es un directorio",
    _errno.EISDIR: "{path} existe y es un directorio, no un fichero",
    _errno.ENOENT: "{path}: la ruta no existe y no se ha podido crear",
    _errno.ENAMETOOLONG: "{path}: la ruta es demasiado larga para este sistema de ficheros",
    _errno.EDQUOT: "cuota de disco agotada al escribir {path}: libera espacio o amplía la cuota",
}


def _describe_os_error(exc: OSError, path: str) -> str:
    """Convierte un fallo del sistema de ficheros en una frase accionable.

    Siempre nombra la RUTA —sin ella el operador no sabe dónde mirar— y añade el
    ``errno`` simbólico al final para los casos que no están en la tabla: un
    mensaje genérico con `ELOOP` dentro sigue siendo mil veces más útil que un
    traceback, y evita que la tabla tenga que ser exhaustiva para ser útil.
    """

    target = getattr(exc, "filename", None) or path
    remedy = _OS_ERROR_REMEDIES.get(exc.errno or -1)
    if remedy is not None:
        return remedy.format(path=target)
    name = _errno.errorcode.get(exc.errno or -1, str(exc.errno))
    return f"no se pudo escribir en {target}: {exc.strerror or exc} ({name})"


def _admin_user_existed_from(lines: list[str]) -> bool | None:
    """¿Creó ``init_tenant`` el usuario admin, o ya existía? ``None`` = no consta.

    ``init_tenant`` es idempotente por diseño y su docstring lo dice por escrito:
    «the password of an existing user is left untouched». El instalador, en
    cambio, mintea una contraseña nueva en CADA ejecución. Sobre una base de
    datos donde el admin ya existe —un reintento sobre datos conservados— la
    contraseña que se revelaría no la ha visto nunca la base de datos: el
    operador la guarda, el instalador se autodestruye, y en el primer login
    recibe credenciales inválidas sin ninguna pista.

    El dato ya viaja: ``init_tenant`` cierra con ``log.info("init_tenant.completed",
    …, created_user=…)`` y el logging del api-server emite JSON de una línea por
    registro. Se busca dentro de la línea (``find``/``rfind`` de las llaves) y no
    con ``json.loads`` de la línea entera para sobrevivir a un prefijo de
    Compose. Si el marcador no aparece se devuelve ``None`` — «no lo sé», que es
    una respuesta distinta de «salió bien» y el revelado la trata distinto.
    """

    for line in lines:
        if _SEED_MARKER_EVENT not in line:
            continue
        start, end = line.find("{"), line.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            payload = json.loads(line[start : end + 1])
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("event") == _SEED_MARKER_EVENT:
            created = payload.get("created_user")
            if isinstance(created, bool):
                return not created
    return None


@dataclass(frozen=True)
class BootstrapReveal:
    """Lo que el one-shot de finalización cuenta de sí mismo, ya parseado.

    ``__repr__`` va redactado por la misma razón que el de
    :class:`~installer_backend.vault_bootstrap.VaultInitResult`: aquí dentro hay
    cinco unseal keys, un root token y una contraseña de admin que se muestran
    EXACTAMENTE UNA VEZ y no tienen recuperación, y un frame de traceback o una
    línea de log suelta bastarían para dejarlos escritos donde nadie los borra.
    """

    already_initialized: bool
    unseal_keys: tuple[str, ...]
    root_token: str
    key_threshold: int
    kv_mount: str
    kv_enabled: bool
    policies_written: tuple[str, ...]
    admin_password: str
    #: ``None`` = el revelado no lo declara. NO es lo mismo que ``False``; ver
    #: :meth:`RealStepExecutor.admin_password_advisories`.
    admin_user_created: bool | None

    def __repr__(self) -> str:  # pragma: no cover - trivial, security-load-bearing
        return (
            "BootstrapReveal(already_initialized="
            f"{self.already_initialized}, kv_mount={self.kv_mount!r}, "
            f"kv_enabled={self.kv_enabled}, policies_written={self.policies_written!r}, "
            "<resto redactado: se muestra una vez, sin recuperación>)"
        )

    __str__ = __repr__

    @property
    def secret_values(self) -> tuple[str, ...]:
        """Todo lo que NO puede aparecer en una línea de progreso ni en un error."""

        return tuple(v for v in (self.root_token, self.admin_password, *self.unseal_keys) if v)

    def as_init(self) -> VaultInitResult | None:
        """El init de Vault, o ``None`` si esta pasada no inicializó nada.

        Un Vault ya inicializado NO se re-inicializa —sería destructivo y sin
        recuperación—, así que en ese caso no hay material nuevo que revelar y el
        revelado tiene que fallar ruidosamente en vez de enseñar nada.
        """

        if self.already_initialized or not self.unseal_keys:
            return None
        return VaultInitResult(
            unseal_keys=self.unseal_keys,
            root_token=self.root_token,
            key_threshold=self.key_threshold,
        )


def _parse_bootstrap_reveal(lines: list[str]) -> BootstrapReveal | None:
    """Extrae la línea de revelado del stdout del one-shot. ``None`` si no está.

    Se busca por el marcador y se recortan las llaves DENTRO de la línea, igual
    que :func:`_admin_user_existed_from` y por el mismo motivo: la salida real
    llega con el prefijo que antepone Compose (``bootstrap-1  | {...}``), así que
    un ``json.loads`` de la línea entera no parsearía nada.
    """

    for line in lines:
        if BOOTSTRAP_REVEAL_EVENT not in line:
            continue
        start, end = line.find("{"), line.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            payload = json.loads(line[start : end + 1])
        except ValueError:
            continue
        if not isinstance(payload, dict) or payload.get("event") != BOOTSTRAP_REVEAL_EVENT:
            continue
        created = payload.get("admin_user_created")
        try:
            return BootstrapReveal(
                already_initialized=bool(payload.get("already_initialized", False)),
                unseal_keys=tuple(str(k) for k in payload.get("unseal_keys") or ()),
                root_token=str(payload.get("root_token") or ""),
                key_threshold=int(payload.get("key_threshold") or 0),
                kv_mount=str(payload.get("kv_mount") or ""),
                kv_enabled=bool(payload.get("kv_enabled", False)),
                policies_written=tuple(str(x) for x in payload.get("policies_written") or ()),
                admin_password=str(payload.get("admin_password") or ""),
                admin_user_created=created if isinstance(created, bool) else None,
            )
        except (TypeError, ValueError):
            # Un revelado con la forma equivocada es exactamente igual de inútil
            # que no tenerlo, y el paso ya sabe qué hacer con «no lo hay».
            continue
    return None


def _redact_bootstrap_output(raw: list[str], reveal: BootstrapReveal | None) -> list[str]:
    """La salida del one-shot que SÍ se puede enseñar.

    Dos filtros, y el segundo no es redundante. El primero quita la línea de
    revelado por su marcador, que es el contrato. El segundo quita cualquier
    línea que contenga uno de los valores revelados, y cubre el caso en que la
    otra mitad además los imprima en claro «para que se vean mejor». Se escribió
    cuando esa mitad no existía —fiar una propiedad de seguridad a un módulo que
    aún nadie ha escrito no es fiarla a nadie— y se conserva ahora que sí
    existe, por lo mismo: `api_server.bootstrap` se redacta a sí mismo, pero una
    propiedad de seguridad que depende de que las DOS mitades acierten a la vez
    se queda con la disciplina de las dos.
    """

    secrets = reveal.secret_values if reveal is not None else ()
    return [
        line
        for line in raw
        if BOOTSTRAP_REVEAL_EVENT not in line and not any(s in line for s in secrets)
    ]


@dataclass
class RealStepExecutor:
    """The real :class:`StepExecutor` binding (see module docstring)."""

    compose_dir: str
    runner: CommandRunner
    env_writer: EnvFileWriter
    tree: DataTreeProvisioner
    cfg: InstallerConfig
    secrets: GeneratedSecrets
    monitoring: bool = False
    #: VESTIGIO. **Ningún paso lo llama** desde que la finalización pasó a correr
    #: dentro de la red del stack: este ejecutor ya no habla con Vault. Sigue
    #: aceptándose porque ``reinstall.build_preserve_executor`` todavía lo pasa
    #: —donde ya venía comentado como «nunca se llama», porque
    #: ``PRESERVE_STEP_ORDER`` no incluye BOOTSTRAP_VAULT—, y quitarlo aquí
    #: rompería ese módulo desde otro fichero. **Se retira junto con
    #: ``real_bindings.build_hvac_vault_client`` en cuanto ese llamador deje de
    #: pasarlo**; no queda ningún otro.
    vault_client_factory: Callable[[InstallerConfig], VaultClient] | None = None
    #: Lector de ficheros del host. Sólo lo usa ``tls_mode: provided``, para
    #: copiar el par certificado+clave que el operador declaró en el
    #: ``install.yaml`` — dos rutas que la validación EXIGE y que hasta el
    #: 2026-08-27 no leía ni una línea del repositorio.
    file_reader: FileReader = field(default_factory=RealFileReader)
    #: Depósito de emergencia de las unseal keys (:mod:`installer_backend.key_escrow`).
    #: ``None`` significa SIN red: si el proceso muere entre el init de Vault y
    #: el revelado, esas claves no vuelven a existir por ningún camino. El CLI
    #: siempre lo cablea; hay una guarda que lo comprueba.
    key_escrow: KeyEscrow | None = None
    #: Unseal keys que aporta el operador (``--vault-unseal-keys-from``) para
    #: reintentar sobre un Vault YA inicializado y sellado. Sin ellas, ese
    #: reintento muere y la única salida documentada era purgar la instalación.
    existing_unseal_keys: tuple[str, ...] = ()

    #: Captured for the one-time credential reveal (read by RealCredentialBuilder).
    vault_bootstrap_result: VaultBootstrapResult | None = field(default=None, init=False)
    #: El revelado del one-shot, entero. Lo escribe BOOTSTRAP_VAULT y lo lee
    #: SEED_TENANT para rendir cuentas sin volver a sembrar. ``None`` = el
    #: one-shot no ha corrido (o no llegó a revelar), que NO es un estado válido
    #: para dar la instalación por buena.
    bootstrap_reveal: BootstrapReveal | None = field(default=None, init=False)
    seeded_admin_password: str | None = field(default=None, init=False)
    #: ¿El usuario admin ya existía cuando corrió la siembra? ``None`` = la
    #: siembra no ha corrido, o su marcador no se pudo leer. Los tres estados son
    #: distintos para el revelado; ver :meth:`admin_password_advisories`.
    seeded_admin_user_existed: bool | None = field(default=None, init=False)

    @property
    def _compose_file(self) -> str:
        return f"{self.compose_dir}/docker-compose.yml"

    def _compose(self, *args: str) -> list[str]:
        return ["docker", "compose", "-p", PROJECT_NAME, "-f", self._compose_file, *args]

    def _write(self, path: str, content: str, *, mode: int) -> None:
        """Escribe a través del seam, traduciendo el fallo del sistema de ficheros.

        TODA escritura de este ejecutor pasa por aquí. Es un método y no un
        ``try`` por sitio precisamente porque el defecto que arregla es de
        omisión: bastaba con dejar UNA escritura sin envolver para que el
        operador volviera a recibir un traceback.
        """

        try:
            self.env_writer.write(path, content, mode=mode)
        except OSError as exc:
            raise StepExecutionError(_describe_os_error(exc, path)) from exc

    #: Servicios cuyo log se recoge cuando `up --wait` falla, aunque compose no
    #: los nombre. Son los que TODO lo demás espera por `depends_on: healthy`:
    #: si uno de éstos no arranca, los veinte de detrás quedan en `Created` y el
    #: error real está aquí y en ningún otro sitio.
    _CIMIENTOS = ("postgres", "redis", "vault", "minio")

    def _arrancar_el_stack(self, lines: list[str]) -> None:
        """`up -d --wait`, y si falla, POR QUÉ falló.

        `up --wait` informa del ESTADO de cada contenedor —`Started`, `Error`—
        y de nada más. Eso deja un mensaje que nombra al culpable sin decir qué
        le pasa: «Container agentic-platform-postgres-1 Error» y a reproducirlo
        a mano. Medido en el e2e (run 33170713059, 2026-08-28): dos servicios en
        `Error` y cero líneas de sus logs en toda la ejecución.

        Así que al fallar se recogen los logs de los servicios que no llegaron a
        sanos. Es una llamada más a `docker compose`, sólo en el camino de
        error, y convierte el fallo en accionable sin depender de que quien lo
        ejecute tenga un paso de diagnóstico preparado — el operador de un
        cliente no lo tiene.
        """
        result = self.runner.run(
            self._compose("up", "-d", "--wait"),
            cwd=self.compose_dir,
            on_line=lines.append,
        )
        if result.returncode == 0:
            return

        partes = [
            f"el comando falló (rc={result.returncode}): "
            f"{' '.join(self._compose('up', '-d', '--wait'))}",
            self._cola_del_fallo(result),
        ]
        sospechosos = self._servicios_en_error(result) or list(self._CIMIENTOS)
        partes.append(
            f"\nLogs de los servicios que no llegaron a sanos ({', '.join(sospechosos)}):"
        )
        for servicio in sospechosos:
            partes.append(self._log_de(servicio))
        raise StepExecutionError("\n".join(partes))

    #: Lo que compose escribe cuando un ONE-SHOT (`migrations`, `bootstrap`) sale
    #: distinto de cero. NO termina en `Error`, así que la primera versión de
    #: este recolector lo perdía: el e2e run 33180241225 murió por
    #: `migrations … exit 1` y el mensaje enseñó los logs de los cuatro cimientos
    #: —los tres sanos— y ni una línea del servicio que había fallado.
    #:
    #: Un recolector que mira sólo una de las dos formas de fallar es peor que
    #: ninguno: no calla, enseña lo que no toca.
    _FALLO_DE_ONE_SHOT = "didn't complete successfully"

    #: Las TRES formas en que `docker compose up --wait` dice que algo falló.
    #: Se enumeran porque cada una costó una ejecución del e2e descubrirla, y
    #: escribirlas juntas es lo que impide seguir parcheándolas de una en una:
    #:
    #:   Container …-postgres-1  Error                          (larga vida)
    #:   Container …-migrations-1  service "migrations" didn't
    #:     complete successfully: exit 1                        (one-shot)
    #:   container …-cortex-beat-1 is unhealthy                 (minúscula!)
    #:
    #: La tercera llega en MINÚSCULA y sin el formato tabulado de las otras dos.
    #: No es un capricho de compose: la emite otra parte de su código.
    _MARCAS_DE_FALLO = ("error", "is unhealthy", "didn't complete successfully")

    @staticmethod
    def _servicios_en_error(result: CommandResult) -> list[str]:
        """Los servicios que compose dio por fallidos, en sus tres formas.

        Se leen de lo que compose ya dijo en vez de volver a preguntar: si el
        stack se cayó del todo, un `ps` posterior puede devolver otra cosa.
        """
        vistos: list[str] = []
        for linea in result.output_lines:
            texto = linea.strip()
            bajo = texto.lower()
            if "container" not in bajo:
                continue
            # Las dos marcas explícitas valen en cualquier posición; la palabra
            # «Error» sólo cuenta al FINAL de la línea, porque compose la usa
            # como estado y dentro de una frase es ruido.
            explicita = any(marca in bajo for marca in ("is unhealthy", "didn't complete"))
            if not explicita and not texto.endswith("Error"):
                continue

            # Cuando compose cita el SERVICIO entre comillas no hay que deducir
            # nada del nombre del contenedor.
            if '"' in texto and RealStepExecutor._FALLO_DE_ONE_SHOT in texto:
                nombre = texto.split('"')[1]
            else:
                # El token siguiente a «container» es el nombre del contenedor.
                resto = bajo.split("container", 1)[1].strip()
                contenedor = resto.split()[0] if resto.split() else ""
                # `agentic-platform-postgres-1` -> `postgres`; los dos proxies no
                # llevan el prefijo del proyecto (`agentic-egress-proxy`).
                nombre = contenedor.removeprefix(f"{PROJECT_NAME}-").removeprefix("agentic-")
                if nombre.rsplit("-", 1)[-1].isdigit():
                    nombre = nombre.rsplit("-", 1)[0]
            if nombre and nombre not in vistos:
                vistos.append(nombre)
        return vistos

    def _log_de(self, servicio: str, *, lineas: int = 40) -> str:
        """El log de un servicio, o el motivo de que no se pudiera leer."""
        recogido: list[str] = []
        salida = self.runner.run(
            self._compose("logs", "--no-color", f"--tail={lineas}", servicio),
            cwd=self.compose_dir,
            on_line=recogido.append,
        )
        cuerpo = [linea.rstrip() for linea in recogido if linea.strip()]
        if not cuerpo:
            motivo = (
                "sin salida"
                if salida.returncode == 0
                else f"`docker compose logs` falló (rc={salida.returncode})"
            )
            return f"--- {servicio}: {motivo}"
        return f"--- {servicio}:\n" + "\n".join(f"  | {linea}" for linea in cuerpo)

    @staticmethod
    def _cola_del_fallo(result: CommandResult, *, maximo: int = 25) -> str:
        """Las últimas líneas de un comando que falló, o por qué no hay ninguna."""
        utiles = [linea.rstrip() for linea in result.output_lines if linea.strip()]
        if not utiles:
            return "(el comando no escribió nada en stdout ni en stderr)"
        recorte = utiles[-maximo:]
        cabecera = (
            f"últimas {len(recorte)} líneas de {len(utiles)}:"
            if len(utiles) > len(recorte)
            else "salida:"
        )
        return cabecera + "\n" + "\n".join(f"  | {linea}" for linea in recorte)

    def _run(
        self,
        args: list[str],
        lines: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        result = self.runner.run(args, cwd=self.compose_dir, env=env, on_line=lines.append)
        if result.returncode != 0:
            # El comando SOLO no basta, y costó una ejecución entera del e2e
            # descubrirlo (run 33169724473, 2026-08-28): el install murió en
            # `start_stack` y el mensaje decía «el comando falló (rc=1): docker
            # compose … up -d --wait» y nada más. Qué servicio no arrancó, o por
            # qué, se lo quedaba el instalador — y el runner LO TENÍA capturado
            # en `output_lines` desde el principio.
            #
            # Un instalador que dice «falló» sin decir qué obliga a reproducir a
            # mano lo que acaba de pasar delante de él. En casa de un cliente,
            # eso es una llamada de soporte por cada fallo.
            raise StepExecutionError(
                f"el comando falló (rc={result.returncode}): {' '.join(args)}\n"
                + self._cola_del_fallo(result)
            )

    def execute(self, step: InstallStep, config: dict[str, object]) -> list[str]:  # noqa: ARG002
        lines: list[str] = []
        match step:
            case InstallStep.GENERATE_CONFIG:
                self._generate_config(lines)
            case InstallStep.PULL_IMAGES:
                self._run(self._compose("pull"), lines)
            case InstallStep.START_STACK:
                self._arrancar_el_stack(lines)
            case InstallStep.RUN_MIGRATIONS:
                # The apps depend_on the one-shot `migrations` with
                # service_completed_successfully, so `up --wait` above already
                # applied them; this explicit step is the AUDITABLE migration
                # gate (matches the upgrade runbook) and is a safe idempotent
                # no-op here (env.py takes a pg_advisory_xact_lock).
                self._run(self._compose("run", "--rm", "migrations"), lines)
            case InstallStep.BOOTSTRAP_VAULT:
                self._bootstrap_vault(lines)
            case InstallStep.SEED_TENANT:
                self._seed_tenant(lines)
            case _:  # pragma: no cover - defensive; every step is handled above
                raise StepExecutionError(f"paso de instalación desconocido: {step}")
        return lines

    # -- steps --------------------------------------------------------------
    def _generate_config(self, lines: list[str]) -> None:
        prod = self.cfg.system.environment is Environment.PRODUCTION

        compose_yaml = render_compose_yaml(generate_compose(self.cfg, monitoring=self.monitoring))
        self._write(self._compose_file, compose_yaml, mode=0o640)
        lines.append("Escrito docker-compose.yml")

        env_text = generate_env_file(self.cfg, self.secrets, monitoring=self.monitoring)
        if prod:
            try:
                assert_env_passes_prod_secret_guard(env_text)
            except ValueError as exc:
                raise StepExecutionError(str(exc)) from exc
        self._write(f"{self.compose_dir}/.env", env_text, mode=0o600)
        lines.append("Escrito .env (0600)")

        global_yaml = render_global_yaml(
            generate_global_config(self.cfg, monitoring=self.monitoring)
        )
        self._write(f"{self.compose_dir}/config/global.yaml", global_yaml, mode=0o640)
        lines.append("Escrito config/global.yaml")

        self._write(f"{self.compose_dir}/caddy/Caddyfile", generate_caddyfile(self.cfg), mode=0o644)
        lines.append("Escrito caddy/Caddyfile")

        # Los auxiliares que el compose monta y que NADIE escribía hasta el
        # 2026-08-27: los scripts de inicialización de Postgres, la config de
        # Vault, los perfiles seccomp, los dos contextos de build de los tinyproxy
        # y —con la superposición— la configuración de monitorización. Viajan
        # dentro del paquete (`installer_backend.stack_assets`) porque en el
        # destino no hay ningún `docker/` del que copiarlos: el compose se escribe
        # bajo la raíz de datos y la imagen del instalador sólo lleva `src/`.
        assets = stack_assets.assets_for(monitoring=self.monitoring)
        for asset in assets:
            self._write(
                f"{self.compose_dir}/{STACK_ASSETS_DIR_NAME}/{asset.path}",
                stack_assets.read_text(asset),
                mode=asset.mode,
            )
        lines.append(f"Escritos {len(assets)} auxiliares en {STACK_ASSETS_DIR_NAME}/")

        plan = build_data_tree_plan(self.cfg, monitoring=self.monitoring)
        # Los directorios de `stack/` que ningún auxiliar trae consigo (un bind
        # que monta un directorio vacío) cuelgan del directorio del COMPOSE, que
        # es contra lo que resuelven sus `./stack/…`.
        plan += build_stack_dirs_plan(self.compose_dir, monitoring=self.monitoring)
        try:
            self.tree.provision(plan)
        except OSError as exc:
            # La otra mitad del mismo defecto: los `write` crean sus directorios
            # padre sobre la marcha, pero el árbol declara rutas con modos
            # concretos, y es AQUÍ donde revienta un /data de otro dueño.
            raise StepExecutionError(_describe_os_error(exc, self.compose_dir)) from exc
        lines.append(f"Árbol de datos creado: {len(plan)} directorios")

        # Va al final, después de `provision`: el directorio del bind existe ya
        # con su 0700 declarado, así que la clave privada no pasa ni un instante
        # dentro de un directorio con el modo del umask.
        self._apply_provided_tls(lines)

    def _apply_provided_tls(self, lines: list[str]) -> None:
        """``tls_mode: provided`` — pone el par corporativo donde Caddy lo monta.

        La avería que cierra: la validación EXIGE ``tls_cert_path`` y
        ``tls_key_path`` (sin ellos rechaza el ``install.yaml``), el instalador
        creaba ``{data_root}/caddy/tls`` **vacío** a 0700, generaba un Caddyfile
        que declara ``tls /etc/caddy/tls/server.crt …`` y montaba ese directorio
        vacío. Caddy no encontraba el certificado, nunca pasaba a healthy,
        ``up -d --wait`` fallaba y con él la instalación entera — y como Caddy es
        el ÚNICO servicio publicado, no quedaba ni por dónde mirar desde el
        navegador. El operador no tiene motivo para sospechar del certificado:
        lo configuró él.

        Dos caminos, porque hay dos formas de instalar:

        * **desde el host** — las rutas del ``install.yaml`` son alcanzables y el
          par se COPIA (``server.crt`` 0644, ``server.key`` 0600);
        * **dentro del contenedor** (``generate``) — sólo está montada la raíz de
          datos, así que esas rutas del host no existen. Ahí lo correcto es
          comprobar que el par YA está en el bind, y decirlo.

        Si no se cumple ninguno de los dos, se falla **ahora**, en el paso 1, con
        las rutas en el mensaje. Fallar tarde le enseña al operador
        ``caddy unhealthy``, que apunta a cualquier otra cosa.
        """

        if self.cfg.system.tls_mode != "provided":
            return

        bind_dir = f"{self.compose_dir}/{_TLS_BIND_SUBDIR}"
        # (origen declarado, destino en el bind, modo). La clave privada NO puede
        # heredar el modo del certificado.
        pairs = (
            (self.cfg.system.tls_cert_path or "", f"{bind_dir}/{_TLS_CERT_NAME}", 0o644),
            (self.cfg.system.tls_key_path or "", f"{bind_dir}/{_TLS_KEY_NAME}", 0o600),
        )

        if all(src and self.file_reader.exists(src) for src, _dest, _mode in pairs):
            for src, dest, mode in pairs:
                try:
                    content = self.file_reader.read_text(src)
                except OSError as exc:
                    raise StepExecutionError(
                        f"no se pudo leer el material TLS declarado en {src}: "
                        f"{_describe_os_error(exc, src)}"
                    ) from exc
                self._write(dest, content, mode=mode)
            lines.append(
                f"Certificado TLS corporativo copiado a {_TLS_BIND_SUBDIR}/ "
                f"({_TLS_CERT_NAME} 0644, {_TLS_KEY_NAME} 0600)"
            )
            return

        if all(self.file_reader.exists(dest) for _src, dest, _mode in pairs):
            lines.append(
                f"Certificado TLS corporativo ya presente en {_TLS_BIND_SUBDIR}/: "
                "se usa el que hay (las rutas del install.yaml no son alcanzables "
                "desde aquí)"
            )
            return

        raise StepExecutionError(
            "tls_mode: provided, pero no hay certificado que usar. No se ha "
            f"encontrado el par declarado en el install.yaml ({pairs[0][0] or '(vacío)'} "
            f"y {pairs[1][0] or '(vacío)'}) ni un par ya colocado en {bind_dir}/ "
            f"({_TLS_CERT_NAME} + {_TLS_KEY_NAME}). Sin él Caddy arranca sin "
            "certificado, nunca pasa a healthy y el `up --wait` tumba la "
            "instalación entera. Corrige las rutas, o —si instalas con la imagen "
            f"del instalador, que sólo ve la raíz de datos— deja el par en "
            f"{bind_dir}/ antes de volver a ejecutar."
        )

    def _bootstrap_argv(self) -> list[str]:
        """El comando del one-shot: el MISMO que el banner deja al operador.

        Sin el ``-p``/``-f`` —que el instalador conoce y el operador no— es
        literalmente ``docker compose run --rm bootstrap``. Que sean el mismo es
        el punto: los dos caminos de instalación acaban ejecutando una sola
        implementación de la finalización, no dos que hay que mantener a la par.

        ``run`` activa por sí solo el perfil del servicio que nombra, así que no
        hace falta un ``--profile`` delante (y añadirlo separaría este comando
        del que está impreso en el banner, que es lo que no interesa).

        El ``-e`` sólo aparece cuando el operador aportó unseal keys: es un
        PASO A TRAVÉS —el flag lleva el nombre, nunca el valor—, así que ningún
        share llega a ``argv`` ni, por tanto, a ``ps`` o al historial del shell.
        """

        passthrough = ["-e", BOOTSTRAP_UNSEAL_KEYS_ENV] if self.existing_unseal_keys else []
        return self._compose("run", "--rm", *passthrough, BOOTSTRAP_SERVICE)

    def _bootstrap_vault(self, lines: list[str]) -> None:
        """Ejecuta el one-shot de finalización y recoge lo que revela.

        El orden de este método es la parte que importa, y es deliberado: se
        deposita ANTES de mirar el código de salida. El caso que lo obliga es el
        one-shot que inicializa Vault, emite el revelado, se pone a sembrar el
        catálogo built-in —minutos— y muere: si el depósito esperase a `rc == 0`,
        esas cinco unseal keys se irían con el proceso y ese Vault no se podría
        desellar nunca más, porque un Vault ya inicializado NO se re-inicializa.
        """

        raw: list[str] = []
        env = (
            {
                BOOTSTRAP_UNSEAL_KEYS_ENV: BOOTSTRAP_UNSEAL_KEYS_SEPARATOR.join(
                    self.existing_unseal_keys
                )
            }
            if self.existing_unseal_keys
            else None
        )
        # `raw` NO se vuelca en `lines`: lleva el revelado dentro.
        result = self.runner.run(
            self._bootstrap_argv(), cwd=self.compose_dir, env=env, on_line=raw.append
        )
        reveal = _parse_bootstrap_reveal(raw)
        lines.extend(_redact_bootstrap_output(raw, reveal))
        escrowed = self._escrow(reveal, lines)

        if result.returncode != 0:
            raise StepExecutionError(
                self._bootstrap_failed(result.returncode, raw, reveal, escrowed)
            )
        if reveal is None:
            raise StepExecutionError(
                f"el one-shot `{BOOTSTRAP_SERVICE}` terminó con rc=0 pero no emitió "
                f"su línea de revelado (`{BOOTSTRAP_REVEAL_EVENT}`), así que no hay "
                "unseal keys, ni root token, ni contraseña de admin que enseñar. La "
                "instalación se para AQUÍ, con el stack entero y tú delante: seguir "
                "hasta el final para descubrirlo cuando el instalador ya se ha "
                "autodestruido sería mucho peor."
            )

        self.bootstrap_reveal = reveal
        init = reveal.as_init()
        self.vault_bootstrap_result = VaultBootstrapResult(
            init=init,
            already_initialized=reveal.already_initialized,
            kv_mount=reveal.kv_mount,
            kv_enabled=reveal.kv_enabled,
            policies_written=reveal.policies_written,
        )
        self.seeded_admin_password = reveal.admin_password or None
        # El campo del revelado manda; el marcador que `init_tenant` emite por su
        # cuenta dentro del mismo contenedor es el respaldo. Si no consta por
        # ninguna vía, la respuesta es `None` = «no lo sé», que el revelado trata
        # distinto de «salió bien».
        self.seeded_admin_user_existed = (
            (not reveal.admin_user_created)
            if reveal.admin_user_created is not None
            else _admin_user_existed_from(raw)
        )

        lines.append("Vault inicializado" if init is not None else "Vault ya inicializado")
        lines.append(f"KV v2 habilitado: {reveal.kv_enabled}")
        lines.append(f"Políticas escritas: {len(reveal.policies_written)}")

    def _escrow(self, reveal: BootstrapReveal | None, lines: list[str]) -> str | None:
        """Deposita las unseal keys recién acuñadas. Devuelve la ruta, o ``None``.

        Un fallo del sistema de ficheros aquí **no tumba la instalación**, y la
        asimetría es deliberada: las claves siguen vivas en memoria y el revelado
        del final las va a enseñar igual, así que abortar por no poder escribir
        la RED DE SEGURIDAD garantizaría la pérdida que la red existe para
        evitar. Lo que sí hace es gritarlo, porque el operador tiene que saber
        que a partir de aquí está sin red: si esta ejecución muere antes del
        revelado, esas cinco claves no vuelven a existir.
        """

        if reveal is None or self.key_escrow is None:
            return None
        init = reveal.as_init()
        if init is None:
            return None
        try:
            path = self.key_escrow.store_init(init)
        except OSError as exc:
            lines.append(
                "AVISO: no se ha podido depositar la copia de emergencia de las "
                f"unseal keys ({_describe_os_error(exc, 'el depósito de claves')}). "
                "La instalación SIGUE y el revelado del final las mostrará, pero "
                "hasta entonces sólo existen en memoria: si este proceso muere "
                "antes, ese Vault no se podrá desellar nunca más. NO cierres esta "
                "sesión."
            )
            return None
        lines.append(
            f"Unseal keys depositadas en {path} (0600) hasta el revelado. "
            "Si esta ejecución no llega al final, sácalas de ahí y BÓRRALO."
        )
        return path

    def _bootstrap_failed(
        self,
        returncode: int,
        raw: list[str],
        reveal: BootstrapReveal | None,
        escrowed: str | None,
    ) -> str:
        """El mensaje de un one-shot que falló: qué pasó, y qué hacer ahora.

        Nunca lleva un secreto dentro. La cola de la salida se toma ya redactada,
        porque un fallo POSTERIOR al revelado es justo el caso en que el material
        acaba de pasar por ese mismo stdout.
        """

        safe = _redact_bootstrap_output(raw, reveal)
        if any(_MISSING_MODULE_HINT in line and BOOTSTRAP_ENTRYPOINT in line for line in safe):
            message = (
                f"el one-shot `{BOOTSTRAP_SERVICE}` no pudo arrancar: la imagen del "
                f"api-server no trae el módulo `{BOOTSTRAP_ENTRYPOINT}` que ejecuta. "
                "Es la segunda mitad del paso 8 del ADR 0161, y aterrizó el "
                "2026-08-28: si ves esto, la imagen del api-server que tienes es "
                "ANTERIOR a esa fecha. Reconstrúyela o baja una etiqueta más nueva; "
                "sin ese módulo no hay init de Vault, ni tenant, ni usuario admin, "
                "ni credenciales, por NINGUNO de los dos caminos de instalación. No "
                "es un problema de tu Docker ni de tu configuración."
            )
        else:
            # La cola por sí sola no sirve cuando el one-shot es CHARLATÁN, y
            # éste lo es: la siembra del catálogo imprime una línea por cada
            # elemento indexado. Con una ventana de 8 líneas el error real se
            # queda fuera y el mensaje enseña ruido de progreso — medido en el
            # e2e run 33193255711, donde el fallo salió con rc=5 (DATABASE) y
            # las ocho últimas líneas eran todas `catalog_ingestion.indexed`.
            #
            # Así que primero se BUSCAN las líneas que se declaran error, y la
            # cola queda como respaldo para cuando no hay ninguna.
            utiles = [line for line in safe if line.strip()]

            def _sin_repetir(lineas: list[str]) -> list[str]:
                """Sin duplicados y en orden. Seis líneas idénticas no informan.

                El one-shot repite el mismo aviso una vez por elemento del
                catálogo: sin esto, la ventana se llena con seis copias de la
                misma frase y el error real sigue sin verse (e2e run 33194504572).
                """
                vistas: list[str] = []
                for linea in lineas:
                    if linea not in vistas:
                        vistas.append(linea)
                return vistas

            # `"level": "error"` y no `"error":`: una línea de log estructurado
            # puede llevar un campo `error` siendo un WARNING —el aviso de
            # ollama lo hace— y colarse como si fuera la causa. El nivel es lo
            # que el propio emisor declara; el campo, sólo un dato suyo.
            severas = _sin_repetir(
                [
                    linea
                    for linea in utiles
                    if '"level": "error"' in linea
                    or '"level": "critical"' in linea
                    or "Traceback" in linea
                ]
            )
            cola = _sin_repetir(utiles[-40:])[-12:]
            partes: list[str] = []
            if severas:
                partes.append("errores:\n  | " + "\n  | ".join(severas[-6:]))
            # La cola SIEMPRE, aunque haya severas: si el proceso murió sin
            # registrar nada de nivel error —una excepción no capturada, un
            # `exit()` seco— lo único que queda es el final de su salida.
            partes.append(
                ("últimas líneas:\n  | " + "\n  | ".join(cola)) if cola else "(sin salida)"
            )
            detail = "\n" + "\n".join(partes)
            message = (
                f"el one-shot de finalización `{BOOTSTRAP_SERVICE}` falló "
                f"(rc={returncode}). Reprodúcelo con `docker compose run --rm "
                f"{BOOTSTRAP_SERVICE}` desde {self.compose_dir} para ver el error "
                f"entero.{detail}"
            )
        if escrowed is not None:
            message += (
                f" AVISO: Vault SÍ llegó a inicializarse antes del fallo, y sus "
                f"unseal keys están en {escrowed} (0600). Cópialas, BÓRRALO, y "
                "reanuda con --vault-unseal-keys-from apuntando a ese fichero: si "
                "las pierdes, ese Vault no se puede desellar nunca más."
            )
        elif reveal is not None and reveal.as_init() is not None:
            # Vault inicializado Y sin depósito: el one-shot acuñó las claves y
            # no se han podido escribir en ninguna parte. Es irreversible, y
            # callárselo sería dejar al operador reintentando sobre un Vault que
            # ya no se puede abrir — con el mismo error, para siempre.
            message += (
                " AVISO GRAVE: Vault SÍ llegó a inicializarse antes del fallo y "
                "sus unseal keys NO se han podido depositar en disco, así que se "
                "han perdido con el proceso. Ese Vault queda sellado sin "
                "recuperación posible: reintentar la instalación fallará siempre "
                "igual. La única salida es borrar el árbol de Vault de la raíz de "
                "datos y volver a empezar — ver el runbook "
                "docs/06-runbooks/04-disaster-recovery.md."
            )
        return message

    def _seed_tenant(self, lines: list[str]) -> None:
        """Rinde cuentas de la siembra que hizo el one-shot. NO vuelve a sembrar.

        El one-shot hace las tres cosas —init de Vault, siembra y revelado—, así
        que repetir `api_server.seeds` + `init_tenant` desde aquí no sería
        redundante y benigno: sería un DEFECTO. `init_tenant` es idempotente y no
        cambia la contraseña de un usuario que ya existe, de modo que la segunda
        pasada mintearía una contraseña que la base de datos no ha visto nunca y
        marcaría al admin como «ya existía» — el aviso que dice, precisamente,
        que la contraseña revelada no abre la cuenta.

        El paso se conserva porque es un hito visible del pipeline (el wizard lo
        pinta, el CLI lo imprime) y porque ES donde se comprueba que la siembra
        ocurrió. Un paso que pasa de largo en silencio sobre una base de datos
        vacía sería la forma barata de dar por instalado lo que no lo está.
        """

        reveal = self.bootstrap_reveal
        if reveal is None:
            raise StepExecutionError(
                f"la siembra no consta: el one-shot `{BOOTSTRAP_SERVICE}` —que es "
                "quien siembra el catálogo built-in y el tenant inicial— no dejó "
                "revelado en el paso anterior. Sin él no hay tenant de plataforma, "
                "ni catálogo, ni usuario admin, y darlo por sembrado dejaría una "
                "instalación vacía que se anuncia como terminada."
            )
        lines.append(
            f"Catálogo built-in sembrado por el one-shot `{BOOTSTRAP_SERVICE}` "
            "(platform tenant + agentes/equipos/tools)"
        )
        lines.append(f"Tenant inicial creado por el one-shot `{BOOTSTRAP_SERVICE}`")
        if self.seeded_admin_user_existed is True:
            lines.append(
                "El usuario admin YA EXISTÍA: se ha reutilizado y su contraseña "
                "NO ha cambiado (init_tenant es idempotente por diseño)"
            )
        elif self.seeded_admin_user_existed is False:
            lines.append("Usuario admin creado")
        else:
            lines.append("Usuario admin sembrado (no se ha podido confirmar si ya existía)")

    def admin_password_advisories(self) -> tuple[str, ...]:
        """Lo que hay que decirle al operador JUNTO a la contraseña revelada.

        Vacío en el único caso en que la contraseña revelada es la buena: el
        usuario se acaba de crear con ella. En los otros dos el revelado no puede
        callarse — es un revelado de una sola vez, sin recuperación, seguido de
        la autodestrucción del instalador, así que la duda tiene que viajar con
        el dato o se pierde con él.

        Ninguna línea lleva la contraseña dentro: se imprimen junto al revelado,
        pero no SON el revelado.
        """

        if not self.seeded_admin_password:
            return ()
        if self.seeded_admin_user_existed is True:
            return (
                "AVISO: el usuario admin YA EXISTÍA en la base de datos. "
                "`init_tenant` es idempotente y NO cambia la contraseña de un "
                "usuario existente, así que esta ejecución NO la ha cambiado: la "
                "que abre la cuenta sigue siendo la anterior. La contraseña de "
                "arriba NO sirve para entrar.",
            )
        if self.seeded_admin_user_existed is None:
            return (
                "AVISO: no se ha podido confirmar si el usuario admin se ha "
                "creado en esta ejecución o ya existía (no apareció el marcador "
                "`init_tenant.completed` en la salida de la siembra). Si ya "
                "existía, `init_tenant` NO habrá cambiado su contraseña y la de "
                "arriba no abrirá la cuenta: compruébalo antes de dar la "
                "instalación por terminada.",
            )
        return ()
