"""Multi-factor authentication — opt-in second factor (Plan 08).

MFA is ADDED ALONGSIDE the existing auth (local login + OIDC + SAML).
A user WITHOUT a confirmed enrollment logs in EXACTLY as before; only a
user with a confirmed factor gets an extra challenge step after the
password/SSO step succeeds.

task_08_09 ships TOTP (:mod:`api_server.auth.mfa.totp`); WebAuthn lands
in task_08_10 next to it.
"""
