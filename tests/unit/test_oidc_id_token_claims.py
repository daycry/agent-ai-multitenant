"""El ID token del OIDC caduca — y hasta aquí nadie lo miraba.

`gotchas/joserfc-decode-no-valida-exp.md` cerró la trampa en `auth/jwt.py` y
dejó anotado, en su sección «Relacionado», que **el mismo hueco seguía abierto**
en `auth/sso/oidc.py`: `verify_id_token` validaba firma + `iss`/`aud`/`nonce` y
NO validaba `exp`. `joserfc.jwt.decode` solo comprueba la firma; la validación de
claims es un paso aparte (`JWTClaimsRegistry.validate`) que nada te recuerda.

Consecuencia real: un ID token robado de los logs de un proxy, del historial del
navegador o de un IdP mal configurado seguía siendo aceptable **para siempre**.
El `nonce` de un solo uso lo mitiga en el camino feliz —el nonce vive en la
sesión de login y se compara— pero mitigar no es validar: cualquier camino que
reutilice el mismo nonce (un IdP que lo repita, una sesión de login reabierta)
se queda sin ninguna cota temporal.

Estos tests fijan las cuatro reglas y NINGUNO puede pasar vacíamente: cada uno
firma un token de verdad contra un JWKS de verdad y comprueba el resultado.
"""

from __future__ import annotations

import time

import httpx
import pytest
from api_server.auth.sso.oidc import OIDCError, OIDCFlow, ResolvedOIDCConfig
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

_ISSUER = "https://idp.exp.test"
_CLIENT_ID = "exp-client"
_JWKS = f"{_ISSUER}/jwks"
_KID = "exp-key-1"
_NONCE = "nonce-abc"

_SIGNING_KEY = RSAKey.generate_key(2048, parameters={"kid": _KID}, private=True)


def _config() -> ResolvedOIDCConfig:
    return ResolvedOIDCConfig(
        issuer=_ISSUER,
        client_id=_CLIENT_ID,
        client_secret="unused-here",
    )


def _token(**overrides: object) -> str:
    """ID token firmado de verdad. Por defecto: válido y vivo una hora."""
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "subject-123",
        "nonce": _NONCE,
        "iat": now,
        "exp": now + 3600,
    }
    for key, value in overrides.items():
        if value is None:
            claims.pop(key, None)
        else:
            claims[key] = value
    return joserfc_jwt.encode({"alg": "RS256", "kid": _KID}, claims, _SIGNING_KEY)


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url).split("?")[0]
    if url == _ISSUER + "/.well-known/openid-configuration":
        return httpx.Response(
            200,
            json={
                "issuer": _ISSUER,
                "authorization_endpoint": f"{_ISSUER}/authorize",
                "token_endpoint": f"{_ISSUER}/token",
                "userinfo_endpoint": f"{_ISSUER}/userinfo",
                "jwks_uri": _JWKS,
            },
        )
    if url == _JWKS:
        return httpx.Response(200, json={"keys": [_SIGNING_KEY.as_dict(private=False)]})
    raise AssertionError(f"unexpected request to {url}")  # pragma: no cover


def _flow() -> tuple[OIDCFlow, httpx.AsyncClient]:
    OIDCFlow.reset_discovery_cache()
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return OIDCFlow(client), client


async def _verify(id_token: str) -> dict[str, object]:
    flow, client = _flow()
    try:
        return await flow.verify_id_token(_config(), id_token=id_token, expected_nonce=_NONCE)
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_live_id_token_is_accepted() -> None:
    """El control: si esto fallara, los rojos de abajo no probarían nada."""
    claims = await _verify(_token())
    assert claims["sub"] == "subject-123"


@pytest.mark.asyncio
async def test_an_expired_id_token_is_rejected() -> None:
    now = int(time.time())
    with pytest.raises(OIDCError) as excinfo:
        await _verify(_token(exp=now - 60, iat=now - 3600))
    assert "claim" in str(excinfo.value).lower() or "exp" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_an_id_token_that_expired_one_second_ago_is_rejected() -> None:
    """Sin `leeway`: el límite es el límite. Un margen silencioso sería una
    política de seguridad tomada por descuido."""
    now = int(time.time())
    with pytest.raises(OIDCError):
        await _verify(_token(exp=now - 1, iat=now - 10))


@pytest.mark.asyncio
async def test_an_id_token_without_exp_is_rejected() -> None:
    """`exp` es OBLIGATORIO en OIDC Core §2. Sin él, «no ha caducado» es
    indistinguible de «no caduca nunca», que es peor que caducado."""
    with pytest.raises(OIDCError):
        await _verify(_token(exp=None))


@pytest.mark.asyncio
async def test_an_id_token_issued_in_the_future_is_rejected() -> None:
    """`iat` futuro = reloj del IdP mal, o token fabricado. En ninguno de los dos
    casos queremos autenticar a nadie con él."""
    now = int(time.time())
    with pytest.raises(OIDCError):
        await _verify(_token(iat=now + 3600, exp=now + 7200))


@pytest.mark.asyncio
async def test_the_other_checks_still_hold_alongside_the_new_ones() -> None:
    """No-regresión: añadir la validación de claims no puede haberse comido
    iss/aud/nonce, que son los que ya existían."""
    with pytest.raises(OIDCError, match="issuer"):
        await _verify(_token(iss="https://evil.test"))
    with pytest.raises(OIDCError, match="audience"):
        await _verify(_token(aud="another-client"))
    with pytest.raises(OIDCError, match="nonce"):
        await _verify(_token(nonce="not-the-one"))
