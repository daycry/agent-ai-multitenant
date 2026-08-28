"""Qué hay YA bajo la raíz de datos, y qué se puede hacer con ello.

Este módulo contesta una sola pregunta, y la contesta ANTES de que el instalador
escriba el primer byte: **¿esta raíz de datos está vacía, o hay ahí una
instalación cuyos secretos hay que respetar?**

Por qué existe
--------------
El fallo clásico de los instaladores, y hasta el 2026-08-27 estaba entero aquí.
La primera instalación falla tarde —Caddy no arranca porque otro servicio tiene
el 443—, pero para entonces Postgres YA hizo su ``initdb`` con la contraseña del
primer ``.env``. El operador libera el puerto y relanza. El paso 1 volvía a
mintar TODOS los secretos con CSPRNG y sobrescribía el ``.env`` sin mirar si
había uno: nuevo ``POSTGRES_PASSWORD``, nuevo ``MINIO_ROOT_PASSWORD``, nuevas
claves Fernet. A partir de ahí el PGDATA tiene la contraseña vieja y el ``.env``
la nueva, ``up --wait`` no termina nunca, y **cada reintento empeora la
situación** acuñando otra tanda. La contraseña original vivía SÓLO en el ``.env``
que se acaba de pisar: no hay reconciliación posible, y la única salida es
borrarlo todo.

Nada en el log lo insinuaba. El paso 1 decía «Escrito .env (0600)» y seguía.

Las tres reglas
---------------
1. **Si hay un ``.env``, se REUTILIZAN sus secretos.** Es el mismo contrato
   PRESERVE que :mod:`installer_backend.reinstall` ya describía —y de hecho se
   reutiliza su código, :func:`~installer_backend.reinstall.secrets_from_env`,
   para que las dos vías no puedan divergir— pero aplicado donde de verdad se
   ejecuta: en la construcción del instalador, no sólo en el subcomando
   ``reinstall``.
2. **Si hay algo y no se puede leer, se REHÚSA.** Un ``.env`` ilegible no
   significa «aquí no había nada»: significa «aquí hay algo que no sé leer», y
   pisarlo destruye la única copia de los secretos. Lo mismo para un PGDATA
   inicializado sin ``.env`` al lado. La salida es explícita
   (``--force-new-secrets``) y su mensaje dice lo que cuesta.
3. **Nunca se sobrescribe sin copia.** Antes de tocar nada se escribe
   ``.env.bak.<timestamp>`` a 0600 — incluso en el camino ``--force``, porque
   que el operador asuma la pérdida de datos no es motivo para quitarle la
   última copia de los secretos viejos.

Lo que este módulo NO hace
--------------------------
No decide, no escribe el ``.env`` nuevo y no habla con Docker. Devuelve una
:class:`SecretDecision` —los secretos a usar, si se reutilizaron, qué se acuñó
de nuevo y dónde quedó la copia— y unas notas para el operador. Quien las
imprime es el CLI; quien escribe es el ejecutor.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from installer_backend.config_generators import (
    EnvFileWriter,
    GeneratedSecrets,
    generate_secrets,
)
from installer_backend.reinstall import (
    MissingExistingSecretError,
    parse_env_text,
    secrets_from_env,
)

#: El fichero de secretos de la instalación, bajo la raíz de datos (que es
#: también el directorio del compose).
ENV_FILENAME = ".env"

#: Sufijo de la copia previa a cualquier sobrescritura. Lleva marca de tiempo
#: para que dos intentos seguidos no se pisen la copia el uno al otro — que
#: sería la misma avería una capa más abajo.
ENV_BACKUP_SUFFIX = ".bak."

#: El fichero que ``initdb`` escribe en cuanto el cluster existe. Es la señal
#: más barata y más fiable de «aquí hay datos con una contraseña ya fijada»:
#: aparece en el primer arranque de Postgres y no depende de que el stack esté
#: levantado ahora mismo.
PGDATA_MARKER = "postgres/PG_VERSION"

#: El flag que abre la puerta de emergencia. Vive aquí porque los mensajes de
#: rechazo tienen que nombrarlo, y un mensaje que remite a un flag que se
#: renombró es peor que no remitir a ninguno.
FORCE_FLAG = "--force-new-secrets"


class UnsafeOverwriteError(Exception):
    """Sobrescribir la raíz de datos destruiría secretos irrecuperables.

    La lanza :meth:`DataRootInspector.resolve_secrets` cuando encuentra una
    instalación previa que NO puede releer. El mensaje va dirigido al operador:
    dice qué se encontró, por qué no se sigue y cuáles son las dos salidas
    (recuperar el ``.env``, o asumir la pérdida con :data:`FORCE_FLAG`). El CLI
    la mapea a su propio código de salida — nunca se traga.
    """


@runtime_checkable
class FileReader(Protocol):
    """Lee ficheros que una instalación anterior dejó bajo la raíz de datos.

    Un seam de LECTURA, deliberadamente mínimo: ``exists`` para decidir y
    ``read_text`` para leer. Estructuralmente idéntico a
    :class:`installer_backend.reinstall.EnvFileReader`, así que sus dos
    implementaciones valen aquí sin adaptador.
    """

    def exists(self, path: str) -> bool: ...

    def read_text(self, path: str) -> str: ...


@dataclass
class FakeFileReader:
    """Lector de prueba: ``files`` mapea ruta → contenido. No toca disco.

    ``unreadable`` modela el caso que más importa y que un dict no puede
    expresar: un fichero que **existe** y cuya lectura falla (permiso denegado,
    fichero corrupto, E/S). Sin él, el test del rechazo no se puede escribir.
    """

    files: dict[str, str] = field(default_factory=dict)
    unreadable: frozenset[str] = frozenset()

    def exists(self, path: str) -> bool:
        return path in self.files

    def read_text(self, path: str) -> str:
        if path in self.unreadable:
            raise PermissionError(13, "Permission denied", path)
        return self.files[path]


@dataclass(frozen=True)
class SecretDecision:
    """Con qué secretos se va a instalar, y de dónde salen.

    ``secrets`` es lo único que consume el ejecutor. El resto es la explicación,
    y es la mitad que impide que esto vuelva a pasar en silencio:

    * ``reused`` — True si salen de un ``.env`` que ya estaba en disco.
    * ``source`` — la ruta de ese ``.env`` (cadena vacía si se acuñaron nuevos).
    * ``regenerated`` — nombres de VARIABLE (nunca valores) que no estaban en el
      fichero anterior y se han acuñado. Rotarlos no destruye datos, pero no es
      gratis: el secreto JWT tira todas las sesiones abiertas.
    * ``backup_path`` — dónde quedó la copia del ``.env`` anterior, o cadena
      vacía si no había nada que copiar.
    * ``notes`` — las líneas que el CLI imprime. Sin secretos dentro; hay un
      test que lo comprueba campo a campo contra ``GeneratedSecrets``.
    """

    secrets: GeneratedSecrets
    reused: bool
    source: str = ""
    regenerated: tuple[str, ...] = ()
    backup_path: str = ""
    notes: tuple[str, ...] = ()


@runtime_checkable
class SecretResolver(Protocol):
    """Lo que el CLI necesita de un inspector: decidir con qué secretos instalar.

    Existe como Protocol —y no como el tipo concreto— porque el CLI lo recibe
    inyectado: los tests le pasan uno que siempre se niega para ejercitar el
    camino de rechazo sin fabricar un sistema de ficheros roto.
    """

    def resolve_secrets(
        self, data_root: str, *, force_new: bool, monitoring: bool = False
    ) -> SecretDecision:
        """Los secretos con los que instalar sobre *data_root*.

        Lanza :class:`UnsafeOverwriteError` cuando seguir destruiría secretos
        irrecuperables.
        """
        ...


def _timestamp() -> str:
    """Marca de tiempo compacta y ordenable para el nombre de la copia."""

    return time.strftime("%Y%m%dT%H%M%S", time.localtime())


@dataclass
class DataRootInspector:
    """Mira la raíz de datos y decide con qué secretos se instala.

    Dos seams y un reloj: ``reader`` para saber qué hay, ``writer`` para dejar
    la copia del ``.env`` anterior, ``now`` para nombrarla. Construirlo no toca
    el host; el único método que lee y escribe es :meth:`resolve_secrets`, y lo
    hace ANTES de que el pipeline empiece — que es lo que permite negarse sin
    haber dejado nada a medias.
    """

    reader: FileReader
    writer: EnvFileWriter
    now: Callable[[], str] = _timestamp

    def env_path(self, data_root: str) -> str:
        return f"{data_root}/{ENV_FILENAME}"

    def resolve_secrets(
        self,
        data_root: str,
        *,
        force_new: bool,
        monitoring: bool = False,
    ) -> SecretDecision:
        """Devuelve los secretos con los que instalar sobre *data_root*.

        Reutiliza los del ``.env`` existente; acuña nuevos sólo sobre una raíz
        de datos limpia o con :data:`FORCE_FLAG`; y lanza
        :class:`UnsafeOverwriteError` cuando hay una instalación previa que no
        puede releer. Copia el ``.env`` anterior antes de cualquier decisión que
        vaya a pisarlo.
        """

        env_path = self.env_path(data_root)
        has_env = self.reader.exists(env_path)

        if force_new:
            return self._forced(env_path, has_env=has_env)
        if has_env:
            return self._reuse(env_path, monitoring=monitoring)
        self._refuse_if_data_without_secrets(data_root)
        return SecretDecision(secrets=generate_secrets(), reused=False)

    # -- los tres caminos ---------------------------------------------------
    def _reuse(self, env_path: str, *, monitoring: bool) -> SecretDecision:
        """Relee el ``.env`` anterior y reconstruye los secretos desde él."""

        try:
            text = self.reader.read_text(env_path)
        except OSError as exc:
            raise UnsafeOverwriteError(
                f"Hay un {ENV_FILENAME} en {env_path} y NO se ha podido leer ({exc}). "
                "No se sobrescribe: ese fichero es el único sitio donde viven la "
                "contraseña de PostgreSQL, las claves de MinIO y las tres claves "
                "Fernet de esta instalación, y acuñar unas nuevas encima dejaría "
                "los datos existentes ilegibles para siempre. Arregla los permisos "
                "o recupera el fichero (o su copia .env.bak.*) y vuelve a "
                f"ejecutar. Si de verdad quieres empezar de cero ASUMIENDO LA "
                f"PÉRDIDA de los datos que haya en disco, pasa {FORCE_FLAG}."
            ) from exc

        try:
            secrets, regenerated = secrets_from_env(parse_env_text(text), monitoring=monitoring)
        except MissingExistingSecretError as exc:
            raise UnsafeOverwriteError(
                f"{exc} (fichero: {env_path}). Se aborta ANTES de escribir nada: "
                "completar el hueco con un secreto nuevo es justo lo que deja los "
                f"datos huérfanos. Si asumes la pérdida, {FORCE_FLAG}."
            ) from exc

        backup = self._backup(env_path, text)
        notes = [
            f"Reutilizando los secretos del {ENV_FILENAME} que ya había en {env_path}: "
            "esta raíz de datos tiene una instalación previa y regenerarlos la "
            "dejaría inservible.",
            f"Copia del {ENV_FILENAME} anterior en {backup} (0600).",
        ]
        if regenerated:
            notes.append(
                "AVISO: estos secretos no estaban en el fichero anterior y se han "
                f"acuñado NUEVOS: {', '.join(regenerated)}. No hay datos atados a "
                "ellos, pero rotar el secreto JWT cierra todas las sesiones abiertas."
            )
        return SecretDecision(
            secrets=secrets,
            reused=True,
            source=env_path,
            regenerated=regenerated,
            backup_path=backup,
            notes=tuple(notes),
        )

    def _forced(self, env_path: str, *, has_env: bool) -> SecretDecision:
        """La puerta de emergencia: acuña todo de nuevo, pero deja copia y avisa."""

        notes = [
            f"{FORCE_FLAG}: se acuñan secretos NUEVOS. Cualquier dato que ya "
            "hubiera en disco (PostgreSQL, MinIO, columnas cifradas con Fernet) "
            "queda inaccesible con esta configuración.",
        ]
        backup = ""
        if has_env:
            try:
                backup = self._backup(env_path, self.reader.read_text(env_path))
                notes.append(f"Copia del {ENV_FILENAME} anterior en {backup} (0600).")
            except OSError as exc:
                notes.append(
                    f"AVISO: el {ENV_FILENAME} anterior existe en {env_path} y no se "
                    f"pudo copiar ({exc}). Va a ser sobrescrito y sus secretos se "
                    "pierden; cópialo tú antes de continuar si aún los necesitas."
                )
        return SecretDecision(
            secrets=generate_secrets(),
            reused=False,
            backup_path=backup,
            notes=tuple(notes),
        )

    def _refuse_if_data_without_secrets(self, data_root: str) -> None:
        """Red de seguridad: hay datos inicializados y no está su ``.env``.

        El caso real es alguien que borró el ``.env`` «para empezar limpio»
        dejando el árbol de datos. Instalar encima produce un stack que no
        arranca y un diagnóstico imposible —la contraseña que abre ese PGDATA ya
        no existe en ninguna parte—; decirlo produce una decisión.
        """

        marker = f"{data_root}/{PGDATA_MARKER}"
        if not self.reader.exists(marker):
            return
        raise UnsafeOverwriteError(
            f"Hay un PostgreSQL YA inicializado en {data_root} ({PGDATA_MARKER} "
            f"existe) pero no hay {ENV_FILENAME} al lado: los secretos de esa "
            "instalación se han perdido. Acuñar unos nuevos daría un stack que "
            "nunca llega a arrancar, porque la contraseña que abre ese PGDATA no "
            "es ninguna de las que se generen ahora. Recupera el "
            f"{ENV_FILENAME} (o una copia .env.bak.*), o borra los datos y "
            f"empieza de cero — si asumes esa pérdida, {FORCE_FLAG} lo hace "
            "explícito."
        )

    # -- utilidades ---------------------------------------------------------
    def _backup(self, env_path: str, text: str) -> str:
        """Escribe la copia del ``.env`` anterior a 0600 y devuelve su ruta."""

        backup = f"{env_path}{ENV_BACKUP_SUFFIX}{self.now()}"
        self.writer.write(backup, text, mode=0o600)
        return backup
