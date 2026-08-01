"""Per-origin HMAC signature VERIFICATION for incoming webhooks (task_13_08).

This is the INBOUND direction of Plan 13: an external tool (GitHub, Jira,
Sentry, Linear, GitLab) POSTs an event and stamps an HMAC signature header
computed over the EXACT raw body with a shared secret. We re-derive that MAC
with the per-project secret and compare it in constant time, rejecting on any
mismatch / missing header BEFORE any processing (the DDoS / abuse gate).

It is the inverse of Plan 10's OUTGOING webhook signing
(:mod:`notification_dispatcher.webhook_signing`): there WE sign the body we
send (and fold a timestamp + nonce into the signed material, because we own
both ends); here an external sender owns the scheme, so we follow ITS exact
convention per origin. The shared crypto primitive — ``hmac.new(secret, body,
sha256)`` + :func:`hmac.compare_digest` constant-time compare — is identical;
only the header name and the wire encoding of the digest differ per origin.

Supported schemes (all HMAC-SHA256 over the raw request body):

  * **GitHub** (``X-Hub-Signature-256: sha256=<hex>``) — also GitLab when an
    operator configures the secret-token-as-HMAC mode; the de-facto standard
    most senders follow.
  * **Generic** (``X-Signature-256: <hex>``) — a bare lowercase-hex digest for
    senders (Jira/Sentry/Linear via a proxy, custom integrations) that do not
    prefix the algorithm. The same MAC, no ``sha256=`` prefix.

A signature is NEVER trusted unless the secret-derived MAC matches; a missing
or malformed header is a hard reject (the caller maps it to 401). The secret is
the per-project signing secret resolved IN MEMORY
(:mod:`api_server.webhooks.secrets`, Fernet at rest) — it never appears in a
header, the body, a log line, or this module's return value.
"""

from __future__ import annotations

import enum
import hmac
import time
from dataclasses import dataclass
from hashlib import sha256

# ---------------------------------------------------------------------------
# Origins — the closed catalogue of external senders we know how to verify.
# Extend by adding members; never rename existing ones (a persisted
# ``incoming_webhook_configs.origin`` row still references the old value).
# ---------------------------------------------------------------------------


class IncomingWebhookOrigin(enum.StrEnum):
    """An external tool family an incoming webhook can come from (Plan 13).

    The value is the URL path segment (``/webhooks/incoming/<origin>/...``)
    AND the persisted ``incoming_webhook_configs.origin`` discriminator, so it
    selects which signature scheme :func:`verify_incoming_signature` applies.
    """

    GITHUB = "github"
    GITLAB = "gitlab"
    JIRA = "jira"
    SENTRY = "sentry"
    LINEAR = "linear"
    GENERIC = "generic"


# Header that carries the ``sha256=<hex>`` GitHub-style signature. GitHub and
# (in HMAC mode) GitLab use this exact name + ``sha256=`` prefix.
_GITHUB_SIGNATURE_HEADER = "X-Hub-Signature-256"
# Header that carries a bare lowercase-hex digest (no algorithm prefix), for
# the generic scheme + senders fronted by a normalising proxy.
_GENERIC_SIGNATURE_HEADER = "X-Signature-256"
# Algorithm prefix the GitHub-style header carries before the hex digest.
_SHA256_PREFIX = "sha256="

# Which header each origin signs in, and whether the digest carries the
# ``sha256=`` prefix. Jira / Sentry / Linear are configured (via their native
# secret or an HMAC proxy) to sign with the generic bare-hex header here; the
# native verification quirks of each land with the per-origin TEMPLATES in
# task_13_09 — task_13_08 owns the shared HMAC gate.
_SCHEME_BY_ORIGIN: dict[IncomingWebhookOrigin, tuple[str, bool]] = {
    IncomingWebhookOrigin.GITHUB: (_GITHUB_SIGNATURE_HEADER, True),
    IncomingWebhookOrigin.GITLAB: (_GITHUB_SIGNATURE_HEADER, True),
    IncomingWebhookOrigin.JIRA: (_GENERIC_SIGNATURE_HEADER, False),
    IncomingWebhookOrigin.SENTRY: (_GENERIC_SIGNATURE_HEADER, False),
    IncomingWebhookOrigin.LINEAR: (_GENERIC_SIGNATURE_HEADER, False),
    IncomingWebhookOrigin.GENERIC: (_GENERIC_SIGNATURE_HEADER, False),
}


@dataclass(frozen=True, slots=True)
class SignatureVerificationResult:
    """Outcome of :func:`verify_incoming_signature` — ``ok`` plus a reason.

    ``reason`` is one of ``"ok"`` / ``"missing_signature"`` / ``"malformed"`` /
    ``"bad_signature"`` so the caller can log / branch without string-matching.
    It NEVER carries the secret or the computed MAC.
    """

    ok: bool
    reason: str


def signature_header_for(origin: IncomingWebhookOrigin) -> str:
    """Return the request header an ``origin`` puts its signature in."""
    header, _prefixed = _SCHEME_BY_ORIGIN[origin]
    return header


def signature_scheme_for(origin: IncomingWebhookOrigin) -> tuple[str, bool]:
    """Return ``(header_name, sha256_prefixed)`` — the scheme an ``origin`` uses.

    The single source of truth for HOW a given origin's signature is carried on
    the wire: the request header it lives in, and whether the hex digest is
    prefixed with ``sha256=`` (GitHub/GitLab) or bare (the generic scheme).
    Exposed so the per-origin TEMPLATES (task_13_09) can DECLARE the scheme from
    the same table :func:`verify_incoming_signature` enforces, instead of
    duplicating it.
    """
    header, prefixed = _SCHEME_BY_ORIGIN[origin]
    return header, prefixed


def compute_incoming_signature(secret: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of ``body`` under ``secret`` (the value a sender stamps).

    The shared crypto primitive used by every supported origin (the per-origin
    differences are only the header name + the optional ``sha256=`` prefix on
    the wire). Exposed so tests + the per-origin templates (task_13_09) can
    build a correctly-signed request without re-implementing the MAC.
    """
    return hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()


def _parse_signature(origin: IncomingWebhookOrigin, raw: str) -> str | None:
    """Extract the lowercase-hex digest from a raw header value, or None.

    For a prefixed scheme (GitHub/GitLab) the value must be ``sha256=<hex>``;
    a missing/wrong prefix is malformed. For the bare scheme the value is the
    hex digest itself. The result is lowercased so the constant-time compare
    matches regardless of the sender's hex case.
    """
    _header, prefixed = _SCHEME_BY_ORIGIN[origin]
    value = raw.strip()
    if prefixed:
        if not value.lower().startswith(_SHA256_PREFIX):
            return None
        value = value[len(_SHA256_PREFIX) :]
    return value.lower() or None


def verify_incoming_signature(
    *,
    origin: IncomingWebhookOrigin,
    secret: str,
    body: bytes,
    signature_header: str | None,
) -> SignatureVerificationResult:
    """Verify an incoming webhook's HMAC signature against the project secret.

    Steps (cheap rejects first, the constant-time MAC compare is the gate):

      1. **Presence** — a missing/empty signature header is rejected
         (``missing_signature``); the caller maps this to 401, so an unsigned
         request never reaches any processing.
      2. **Well-formedness** — the header parses to a lowercase-hex digest in
         the origin's scheme (``malformed`` otherwise).
      3. **Authentication** — recompute ``HMAC-SHA256(secret, body)`` and
         compare to the presented digest in constant time
         (:func:`hmac.compare_digest`). A tampered body, a wrong secret, or a
         secret belonging to ANOTHER project/tenant all fail here
         (``bad_signature``).

    ``secret`` is the per-project signing secret (resolved in memory; never
    logged). ``body`` is the EXACT raw request bytes — using the raw body, not
    a re-serialised copy, keeps us byte-for-byte identical to what the sender
    signed regardless of JSON key ordering / whitespace.
    """
    if not signature_header:
        return SignatureVerificationResult(ok=False, reason="missing_signature")

    presented = _parse_signature(origin, signature_header)
    if presented is None:
        return SignatureVerificationResult(ok=False, reason="malformed")

    expected = compute_incoming_signature(secret, body)
    if not hmac.compare_digest(expected, presented):
        return SignatureVerificationResult(ok=False, reason="bad_signature")

    return SignatureVerificationResult(ok=True, reason="ok")


# ---------------------------------------------------------------------------
# Anti-replay (authz-5) — ventana de frescura + clave de dedup determinista
# ---------------------------------------------------------------------------
# Cabecera con la marca de tiempo del emisor, por origen. `None` = ese origen
# no la manda y no hay nada que comprobar. Hoy solo la declara el esquema
# `generic`, porque es el único cuya convención es NUESTRA: es la misma
# cabecera que estampa el firmado SALIENTE
# (`notification_dispatcher.webhook_signing.TIMESTAMP_HEADER`), de modo que un
# emisor construido con nuestro propio firmador encaja sin traducción. Los
# demás orígenes se añaden aquí el día que se verifique qué mandan de verdad;
# inventarles una cabecera sería peor que no comprobar nada.
_TIMESTAMP_HEADER_BY_ORIGIN: dict[IncomingWebhookOrigin, str | None] = {
    IncomingWebhookOrigin.GITHUB: None,
    IncomingWebhookOrigin.GITLAB: None,
    IncomingWebhookOrigin.JIRA: None,
    IncomingWebhookOrigin.SENTRY: None,
    IncomingWebhookOrigin.LINEAR: None,
    IncomingWebhookOrigin.GENERIC: "X-Agentic-Timestamp",
}

# Prefijo de la clave de dedup derivada, para que se distinga de un id de
# entrega real del emisor a simple vista en la tabla y en un `SELECT`.
_DERIVED_DELIVERY_PREFIX = "body-sha256:"
# `incoming_webhook_events.delivery_id` es `VARCHAR(255)`. Una cabecera más
# larga no cabe: escribirla tal cual reventaría el INSERT con un DataError
# (500) en un endpoint público, así que se sustituye por su hash — que
# conserva exactamente la propiedad que importa, ser determinista.
_MAX_DELIVERY_ID_CHARS = 255


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    """Resultado de :func:`verify_incoming_freshness` — `ok` + motivo.

    `reason` es `"ok"` / `"no_timestamp"` / `"malformed_timestamp"` /
    `"stale_timestamp"`, para que quien llame ramifique sin comparar textos.
    """

    ok: bool
    reason: str


def timestamp_header_for(origin: IncomingWebhookOrigin) -> str | None:
    """Cabecera de marca de tiempo que declara `origin`, o None si no declara."""
    return _TIMESTAMP_HEADER_BY_ORIGIN[origin]


def verify_incoming_freshness(
    *,
    origin: IncomingWebhookOrigin,
    timestamp_header: str | None,
    max_skew_seconds: int,
    now: int | None = None,
) -> FreshnessResult:
    """Comprueba que la marca de tiempo declarada cae dentro de la ventana.

    Mismo criterio que el verificador saliente
    (`notification_dispatcher.webhook_signing.verify_webhook`): el timestamp
    es epoch en segundos y se acepta si `|now - ts| <= max_skew_seconds` — la
    comparación es en valor absoluto para rechazar también un futuro
    imposible, que es como se cuela un mensaje «que nunca caduca».

    **Qué NO es esto, dicho aquí para que nadie se confíe**: en el esquema
    entrante la firma cubre EXCLUSIVAMENTE el cuerpo, así que esta cabecera no
    está autenticada y quien capture una entrega válida puede reescribirla. La
    frescura sirve contra reintentos rancios de un emisor legítimo (una cola
    que se drena seis horas tarde) y como defensa en profundidad; **el control
    real anti-replay es la clave de dedup** de :func:`derive_delivery_id`, que
    sí se deriva de material autenticado (el cuerpo firmado).

    Ausencia de cabecera → `no_timestamp` con `ok=True`: el origen puede no
    mandarla y no hay nada que verificar. Exigirla rompería a todos los
    emisores existentes sin ganar seguridad, precisamente porque no está
    firmada.
    """
    header = _TIMESTAMP_HEADER_BY_ORIGIN[origin]
    if header is None or not timestamp_header:
        return FreshnessResult(ok=True, reason="no_timestamp")

    try:
        ts = int(timestamp_header.strip())
    except (TypeError, ValueError):
        return FreshnessResult(ok=False, reason="malformed_timestamp")

    moment = int(time.time()) if now is None else now
    if abs(moment - ts) > max_skew_seconds:
        return FreshnessResult(ok=False, reason="stale_timestamp")

    return FreshnessResult(ok=True, reason="ok")


def derive_delivery_id(*, delivery_header: str | None, body: bytes) -> str:
    """Clave de dedup de una entrega. NUNCA devuelve None (authz-5).

    El índice único parcial de `incoming_webhook_events` es
    `(config_id, delivery_id) WHERE delivery_id IS NOT NULL`, así que una
    entrega sin cabecera de delivery —el caso normal del origen `generic`—
    guardaba `NULL`, esquivaba el índice y podía reproducirse infinitas veces:
    cada replay creaba un evento nuevo y volvía a ejecutar su acción (una
    tarea creada, un comentario, un escalado). Ese era el agujero.

    Cuando el emisor manda su id de entrega, ese id manda: es su semántica de
    reintento y dos entregas DISTINTAS con el mismo cuerpo (un `ping`
    repetido) tienen que poder entrar las dos. Cuando no lo manda, la clave se
    deriva del **cuerpo**, que es justo el material que la firma cubre: un
    atacante no puede cambiarlo sin invalidar el MAC, así que no puede
    fabricar una clave nueva para el mismo mensaje.

    El precio, explícito: para un emisor sin id de entrega, dos cuerpos
    idénticos son indistinguibles de un replay y el segundo se responde como
    `duplicate`. Es la consecuencia de no traer id, no un defecto — quien
    necesite mandar el mismo cuerpo dos veces tiene que identificar la
    entrega, que es exactamente lo que se le pide a un webhook.
    """
    if delivery_header:
        if len(delivery_header) <= _MAX_DELIVERY_ID_CHARS:
            return delivery_header
        # Demasiado larga para la columna: se sustituye por su hash, que sigue
        # siendo determinista (el mismo reintento vuelve a colisionar).
        return _DERIVED_DELIVERY_PREFIX + sha256(delivery_header.encode("utf-8")).hexdigest()
    return _DERIVED_DELIVERY_PREFIX + sha256(body).hexdigest()


__all__ = [
    "FreshnessResult",
    "IncomingWebhookOrigin",
    "SignatureVerificationResult",
    "compute_incoming_signature",
    "derive_delivery_id",
    "signature_header_for",
    "signature_scheme_for",
    "timestamp_header_for",
    "verify_incoming_freshness",
    "verify_incoming_signature",
]
