"""`/auth/login` gasta el mismo trabajo Argon2 exista o no el usuario (authz-7).

El oráculo que esto cierra: antes, `login` cortocircuitaba SIN llamar a
`verify_password` cuando el email no existía, cuando el usuario estaba
inactivo o cuando era una identidad SSO. Una verificación argon2id con
`memory_cost=64 MiB` tarda decenas de milisegundos; no hacerla es un canal
lateral de tiempo perfectamente medible desde fuera, y con él se enumera el
padrón de usuarios de la plataforma sin necesidad de acertar ni una
contraseña.

Los tests NO miden latencia de punta a punta (sería flaky y mediría la red,
el pool y el planner). Miden las dos mitades de la propiedad, que juntas la
implican:

  1. **El router gasta exactamente una verificación argon2 en TODAS las ramas**
     — usuario desconocido, inactivo, SSO y contraseña incorrecta. Se cuenta
     interceptando las dos funciones que el router puede llamar.
  2. **La verificación de relleno es trabajo real** — mismo `$argon2id$` y
     mismos parámetros `m`/`t`/`p` que un hash de producción, y tarda lo que
     tarda argon2. Sin esta mitad, la primera pasaría con un `pass`.
"""

from __future__ import annotations

import re
import time

import pytest
from api_server.auth import passwords
from api_server.db.models import User
from api_server.routers.auth import _verify_login_password

# ---------------------------------------------------------------------------
# 1. El router gasta una verificación en todas las ramas
# ---------------------------------------------------------------------------


def _user(**overrides: object) -> User:
    """Un `User` en memoria, sin sesión ni base de datos."""
    attrs: dict[str, object] = {
        "email": "someone@example.com",
        "password_hash": passwords.hash_password("the-real-password"),
        "is_active": True,
        "is_sso_provisioned": False,
    }
    attrs.update(overrides)
    return User(**attrs)


@pytest.fixture()
def argon2_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Cuenta las verificaciones argon2 que hace `_verify_login_password`.

    Se interceptan los dos nombres tal y como el router los importó, así que
    si alguien añade una rama nueva que no gaste ninguna, el recuento lo
    delata.
    """
    seen: list[str] = []

    real_verify = passwords.verify_password
    real_burn = passwords.burn_password_verification

    def counting_verify(plain: str, hashed: str) -> bool:
        seen.append("verify")
        return real_verify(plain, hashed)

    def counting_burn(plain: str) -> None:
        seen.append("burn")
        real_burn(plain)

    monkeypatch.setattr("api_server.routers.auth.verify_password", counting_verify)
    monkeypatch.setattr("api_server.routers.auth.burn_password_verification", counting_burn)
    return seen


@pytest.mark.parametrize(
    ("case", "user_factory"),
    [
        ("desconocido", lambda: None),
        ("inactivo", lambda: _user(is_active=False)),
        ("sso", lambda: _user(is_sso_provisioned=True, password_hash="!sso-no-password")),
        ("password-incorrecta", _user),
    ],
)
def test_every_rejection_path_spends_one_argon2_verification(
    case: str, user_factory, argon2_calls: list[str]
) -> None:
    user = user_factory()

    assert _verify_login_password(user, "wrong-password") is False

    message = f"rama {case!r}: se esperaba exactamente 1 verificación argon2, hubo {argon2_calls}"
    assert len(argon2_calls) == 1, message


def test_the_happy_path_still_verifies_against_the_stored_hash(argon2_calls: list[str]) -> None:
    """La contra-prueba: si `_verify_login_password` devolviera siempre False
    los cuatro tests de arriba pasarían igual y nadie podría entrar."""
    assert _verify_login_password(_user(), "the-real-password") is True
    assert argon2_calls == ["verify"]


def test_an_sso_identity_never_reaches_the_real_verifier() -> None:
    """El `password_hash` de una identidad SSO es un centinela que NO es una
    codificación argon2 válida: pasárselo a `verify_password` levanta
    `ValueError` y devolvería un 500 en vez de un 401. La rama de relleno es
    la que evita a la vez el oráculo de tiempo y ese 500."""
    sso = _user(is_sso_provisioned=True, password_hash="!sso-no-password")

    assert _verify_login_password(sso, "whatever") is False

    with pytest.raises(ValueError):
        passwords.verify_password("whatever", "!sso-no-password")


# ---------------------------------------------------------------------------
# 2. La verificación de relleno es trabajo argon2 real
# ---------------------------------------------------------------------------
_PARAMS = re.compile(r"^\$argon2id\$v=(?P<v>\d+)\$m=(?P<m>\d+),t=(?P<t>\d+),p=(?P<p>\d+)\$")


def test_the_dummy_hash_uses_the_same_parameters_as_a_real_one() -> None:
    """Un relleno con parámetros más baratos que los de producción reabre el
    oráculo: seguiría habiendo diferencia de tiempo medible entre las ramas."""
    real = _PARAMS.match(passwords.hash_password("x"))
    dummy = _PARAMS.match(passwords.dummy_password_hash())

    assert real is not None, "el hash de producción ya no es argon2id"
    assert dummy is not None, "el hash de relleno no es argon2id"
    assert dummy.groupdict() == real.groupdict()


def test_burning_a_verification_costs_argon2_time_and_never_authenticates() -> None:
    """Que no se degrade a un `pass`: argon2id con 64 MiB no baja de unos
    milisegundos ni en la máquina más rápida. Y que nunca devuelva algo que
    un llamante pueda confundir con un éxito, ni siquiera si acierta el texto
    del propio relleno."""
    started = time.perf_counter()
    result = passwords.burn_password_verification("wrong")
    elapsed = time.perf_counter() - started

    assert result is None
    assert elapsed >= 0.005, f"la verificación de relleno tardó {elapsed:.6f}s: no está trabajando"

    # El texto exacto del relleno tampoco autentica nada (devuelve None igual).
    assert passwords.burn_password_verification(passwords._DUMMY_PASSWORD) is None
