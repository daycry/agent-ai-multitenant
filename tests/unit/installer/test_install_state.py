"""Qué hay YA bajo la raíz de datos, y qué se puede hacer con ello.

El modo de fallo que este módulo existe para impedir es el clásico de los
instaladores, y hasta el 2026-08-27 estaba entero: la primera instalación falla
tarde —Caddy no arranca porque otro servicio tiene el 443— pero Postgres ya hizo
su ``initdb`` con la contraseña del primer ``.env``. El operador libera el puerto
y relanza. El paso 1 vuelve a mintar TODOS los secretos con CSPRNG y sobrescribe
el ``.env`` sin mirar si había uno: nuevo ``POSTGRES_PASSWORD``, nuevo
``MINIO_ROOT_PASSWORD``, nuevas claves Fernet. Ahora el PGDATA tiene la
contraseña vieja y el ``.env`` la nueva, ``up --wait`` no termina nunca, y cada
reintento empeora la situación mintando otra tanda. La contraseña original vivía
SÓLO en el ``.env`` que se acaba de pisar: no hay reconciliación posible.

Lo que se afirma aquí, en una línea: **una segunda ejecución sobre la misma raíz
de datos reutiliza los secretos que ya hay, y cuando no puede leerlos se NIEGA a
sobrescribir**. Nunca en silencio, y nunca sin copia previa.
"""

from __future__ import annotations

from dataclasses import fields

import pytest
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import (
    FakeEnvFileWriter,
    GeneratedSecrets,
    generate_env_file,
    generate_secrets,
)
from installer_backend.install_state import (
    ENV_BACKUP_SUFFIX,
    ENV_FILENAME,
    PGDATA_MARKER,
    DataRootInspector,
    FakeFileReader,
    UnsafeOverwriteError,
)
from installer_backend.reinstall import _MONITORING_SECRETS as MONITORING_ONLY_SECRETS
from installer_backend.reinstall import SECRETS_NOT_IN_THE_ENV

pytestmark = pytest.mark.unit

_ROOT = "/data/agent-platform"
_ENV = f"{_ROOT}/{ENV_FILENAME}"


def _inspector(
    files: dict[str, str] | None = None,
    *,
    unreadable: frozenset[str] = frozenset(),
) -> tuple[DataRootInspector, FakeEnvFileWriter]:
    writer = FakeEnvFileWriter()
    reader = FakeFileReader(files=dict(files or {}), unreadable=unreadable)
    return (
        DataRootInspector(reader=reader, writer=writer, now=lambda: "20260828T101500"),
        writer,
    )


def _existing_env(cfg: InstallerConfig, secrets: GeneratedSecrets) -> str:
    """Un ``.env`` escrito por el generador REAL — el fichero que se reencuentra.

    Se genera con el generador de verdad y no con un diccionario a mano porque
    lo que se está afirmando es que el instalador sabe releer **su propia**
    salida: un parser que sólo entiende el `.env` de juguete del test pasaría en
    verde y fallaría con el fichero real.
    """

    return generate_env_file(cfg, secrets)


# ---------------------------------------------------------------------------
# El caso central: relanzar no acuña secretos nuevos
# ---------------------------------------------------------------------------
def test_a_second_run_reuses_the_secrets_of_the_env_already_on_disk(
    installer_config: InstallerConfig,
) -> None:
    """Los secretos atados a datos en disco sobreviven a un segundo intento.

    Son los nueve de ``_DATA_BOUND_SECRETS``: las tres contraseñas de rol de
    Postgres (fijadas por ``initdb`` y por la creación de roles, y NO reaplicadas
    por un ``up`` posterior), el usuario y la clave raíz de MinIO (horneados en
    el almacén de objetos igual), y las tres claves Fernet, que son la ÚNICA
    forma de leer las columnas cifradas. Regenerar cualquiera de ellas no
    «rota» nada: deja datos huérfanos.
    """

    first = generate_secrets()
    inspector, _writer = _inspector({_ENV: _existing_env(installer_config, first)})

    decision = inspector.resolve_secrets(_ROOT, force_new=False)

    assert decision.reused is True
    assert decision.secrets.postgres_password == first.postgres_password
    assert decision.secrets.migrations_user_password == first.migrations_user_password
    assert decision.secrets.app_user_password == first.app_user_password
    assert decision.secrets.service_user_password == first.service_user_password
    assert decision.secrets.minio_root_user == first.minio_root_user
    assert decision.secrets.minio_root_password == first.minio_root_password
    assert decision.secrets.sso_encryption_key == first.sso_encryption_key
    assert decision.secrets.notification_encryption_key == first.notification_encryption_key
    assert decision.secrets.incoming_webhook_encryption_key == first.incoming_webhook_encryption_key


@pytest.mark.parametrize("monitoring", [False, True])
def test_every_secret_the_env_carries_survives_a_second_run(
    installer_config: InstallerConfig, monitoring: bool
) -> None:
    """Barrido sobre TODO ``GeneratedSecrets``, no sobre una lista escrita a mano.

    Una lista se queda corta el día que alguien añade un campo — que es
    exactamente lo que pasó dos veces en agosto de 2026 con
    ``service_user_password`` y ``redis_password``. Aquí el conjunto se deriva
    del dataclass, así que un campo nuevo entra solo en la afirmación: o viaja en
    el ``.env`` y se reutiliza, o está declarado como no recuperable.

    Se parametriza por la superposición de monitorización porque la contraseña
    de Grafana SÓLO se escribe con ella activa: sin parametrizar, la mitad del
    contrato («con overlay también se reutiliza») no la afirmaba nadie, y el
    barrido daba un falso rojo sobre un comportamiento correcto.
    """

    first = generate_secrets()
    env_text = generate_env_file(installer_config, first, monitoring=monitoring)
    inspector, _writer = _inspector({_ENV: env_text})

    decision = inspector.resolve_secrets(_ROOT, force_new=False, monitoring=monitoring)

    # Los secretos que sólo existen con la superposición no están en el .env de
    # una instalación base, así que ahí no hay nada que reutilizar. La lista sale
    # del propio mapa del reinstall, no de un literal aquí.
    absent = set(SECRETS_NOT_IN_THE_ENV)
    if not monitoring:
        absent |= set(MONITORING_ONLY_SECRETS.values())

    for f in fields(GeneratedSecrets):
        if f.name in absent:
            continue
        assert getattr(decision.secrets, f.name) == getattr(first, f.name), (
            f"«{f.name}» se ha regenerado en la segunda ejecución. Si es un "
            "secreto que de verdad no viaja en el .env, decláralo en "
            "SECRETS_NOT_IN_THE_ENV; si viaja, clasifícalo como data-bound o "
            "rotatable — la tercera opción, quedárselo sin clasificar, es la "
            "que deja datos huérfanos en silencio."
        )


def test_the_previous_env_is_copied_before_anything_is_overwritten(
    installer_config: InstallerConfig,
) -> None:
    """Copia a 0600 ANTES de tocar nada, con el contenido exacto del anterior.

    La copia no es un lujo: el ``.env`` es el único sitio del mundo donde vive
    la contraseña de Postgres de una instalación. Se escribe antes de la
    regeneración —no después— porque si el proceso muere a mitad, lo que tiene
    que quedar en disco es el fichero viejo íntegro.
    """

    first = generate_secrets()
    original = _existing_env(installer_config, first)
    inspector, writer = _inspector({_ENV: original})

    decision = inspector.resolve_secrets(_ROOT, force_new=False)

    expected = f"{_ENV}{ENV_BACKUP_SUFFIX}20260828T101500"
    assert decision.backup_path == expected
    assert writer.written[expected] == original
    assert writer.modes[expected] == 0o600


def test_rotatable_secrets_absent_from_the_old_env_are_named(
    installer_config: InstallerConfig,
) -> None:
    """Lo que sí se acuña de nuevo se dice, por su nombre de variable.

    Un ``.env`` escrito antes de que existiera ``API_SERVER_INTERNAL_TOKEN_SECRET``
    (llegó con el ADR 0136) no puede bloquear una reinstalación: ese secreto
    firma, no descifra. Pero rotarlo NO es gratis —cambiar el secreto JWT tira
    todas las sesiones abiertas—, así que el operador tiene que enterarse.
    """

    first = generate_secrets()
    env_text = "\n".join(
        line
        for line in _existing_env(installer_config, first).splitlines()
        if not line.startswith("API_SERVER_JWT_SECRET=")
    )
    inspector, _writer = _inspector({_ENV: env_text})

    decision = inspector.resolve_secrets(_ROOT, force_new=False)

    assert "API_SERVER_JWT_SECRET" in decision.regenerated
    assert decision.secrets.jwt_secret != first.jwt_secret
    assert any("API_SERVER_JWT_SECRET" in note for note in decision.notes)


# ---------------------------------------------------------------------------
# Las tres negativas: antes sobrescribía en silencio en los tres casos
# ---------------------------------------------------------------------------
def test_an_unreadable_env_refuses_to_overwrite_instead_of_minting_new_secrets() -> None:
    """Un ``.env`` que existe pero no se puede leer NO autoriza a acuñar otro.

    Es el peor de los tres casos porque el fichero está delante: un permiso mal
    puesto o un fichero corrupto no significa «aquí no había nada», significa
    «aquí hay algo que no sé leer». Sobrescribirlo destruye la única copia de los
    secretos de la instalación.
    """

    inspector, writer = _inspector({_ENV: "no importa"}, unreadable=frozenset({_ENV}))

    with pytest.raises(UnsafeOverwriteError) as excinfo:
        inspector.resolve_secrets(_ROOT, force_new=False)

    message = str(excinfo.value)
    assert _ENV in message
    assert "--force-new-secrets" in message, "el mensaje debe decir cuál es la salida"
    assert not writer.written, "no se puede escribir NADA cuando se aborta por esto"


def test_an_env_missing_a_data_bound_secret_refuses(
    installer_config: InstallerConfig,
) -> None:
    """Falta ``POSTGRES_PASSWORD`` → parar, no completar el hueco con una nueva.

    Rellenar el hueco es exactamente el defecto: la contraseña que abre ese
    PGDATA es la que falta, y la que se acuñaría no la abre.
    """

    first = generate_secrets()
    env_text = "\n".join(
        line
        for line in _existing_env(installer_config, first).splitlines()
        if not line.startswith("POSTGRES_PASSWORD=")
    )
    inspector, writer = _inspector({_ENV: env_text})

    with pytest.raises(UnsafeOverwriteError) as excinfo:
        inspector.resolve_secrets(_ROOT, force_new=False)

    assert "POSTGRES_PASSWORD" in str(excinfo.value)
    assert not writer.written


def test_initialised_postgres_without_an_env_refuses() -> None:
    """Hay PGDATA y no hay ``.env``: los secretos se han perdido, no se inventan.

    Es la red de seguridad del caso en que alguien borró el ``.env`` «para
    empezar limpio» dejando los datos. Instalar encima produce un stack que no
    arranca y un diagnóstico imposible; decirlo produce una decisión.
    """

    inspector, writer = _inspector({f"{_ROOT}/{PGDATA_MARKER}": "16"})

    with pytest.raises(UnsafeOverwriteError) as excinfo:
        inspector.resolve_secrets(_ROOT, force_new=False)

    message = str(excinfo.value)
    assert PGDATA_MARKER in message
    assert "--force-new-secrets" in message
    assert not writer.written


# ---------------------------------------------------------------------------
# Los dos caminos que sí escriben
# ---------------------------------------------------------------------------
def test_a_clean_data_root_mints_fresh_secrets() -> None:
    """Sin ``.env`` y sin datos: instalación de verdad nueva, sin ceremonias."""

    inspector, writer = _inspector({})

    decision = inspector.resolve_secrets(_ROOT, force_new=False)

    assert decision.reused is False
    assert decision.backup_path == ""
    assert decision.secrets.postgres_password
    assert not writer.written


def test_force_new_secrets_still_copies_the_old_env_first(
    installer_config: InstallerConfig,
) -> None:
    """La puerta de emergencia acuña de nuevo, pero NO destruye la copia.

    ``--force-new-secrets`` existe para el caso en que el operador asume la
    pérdida de datos. Que asuma la pérdida no es motivo para quitarle la última
    copia de los secretos viejos: si se arrepiente a los cinco minutos, el
    fichero de respaldo es lo único que le devuelve el despliegue.
    """

    first = generate_secrets()
    original = _existing_env(installer_config, first)
    inspector, writer = _inspector({_ENV: original})

    decision = inspector.resolve_secrets(_ROOT, force_new=True)

    assert decision.reused is False
    assert decision.secrets.postgres_password != first.postgres_password
    assert writer.written[decision.backup_path] == original
    assert writer.modes[decision.backup_path] == 0o600
    assert any("--force-new-secrets" in note for note in decision.notes)


def test_force_new_secrets_over_an_unreadable_env_does_not_lose_it() -> None:
    """Si ni siquiera se puede leer para copiarlo, ``--force`` avisa y sigue.

    No puede abortar —es la puerta de emergencia, y abortar la dejaría sin
    salida— pero tampoco puede callarse: el operador tiene que saber que el
    fichero viejo está ahí, ilegible, y que va a ser pisado.
    """

    inspector, _writer = _inspector({_ENV: "x"}, unreadable=frozenset({_ENV}))

    decision = inspector.resolve_secrets(_ROOT, force_new=True)

    assert decision.reused is False
    assert decision.backup_path == ""
    assert any("no se pudo copiar" in note.lower() for note in decision.notes)


# ---------------------------------------------------------------------------
# La decisión se le cuenta al operador — y sin secretos dentro
# ---------------------------------------------------------------------------
def test_the_decision_never_carries_a_secret_in_its_notes(
    installer_config: InstallerConfig,
) -> None:
    """Las notas se imprimen en el log del instalador: no pueden llevar valores.

    Es la misma invariante que el resto del CLI («ninguna línea de log lleva un
    secreto»), y aquí es especialmente fácil romperla sin querer, porque el
    módulo entero manipula secretos.
    """

    first = generate_secrets()
    inspector, _writer = _inspector({_ENV: _existing_env(installer_config, first)})

    decision = inspector.resolve_secrets(_ROOT, force_new=False)

    blob = "\n".join(decision.notes)
    for f in fields(GeneratedSecrets):
        value = getattr(first, f.name)
        assert value not in blob, f"la nota del instalador lleva el valor de {f.name}"
    assert decision.notes, "una reutilización SIEMPRE se anuncia; en silencio no vale"
