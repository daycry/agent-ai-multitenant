"""Unit: construcción del entorno de auth git (ADR 0072) — PAT vía GIT_ASKPASS,
SSH vía GIT_SSH_COMMAND, y limpieza de temporales. No persiste el secreto en URL."""

from __future__ import annotations

import os

import pytest
from workers.git_auth import build_git_auth_env

pytestmark = pytest.mark.unit


def test_pat_builds_askpass_env() -> None:
    auth = build_git_auth_env("pat", provider="github", token="ghp_secret")
    try:
        assert auth.env["GIT_USERNAME"] == "x-access-token"  # default GitHub
        assert auth.env["GIT_PASSWORD"] == "ghp_secret"
        assert auth.env["GIT_TERMINAL_PROMPT"] == "0"
        script = auth.env["GIT_ASKPASS"]
        assert os.path.exists(script)
        with open(script, encoding="utf-8") as f:
            content = f.read()
        # El askpass devuelve user para "Username..." y el token en otro caso.
        assert "GIT_USERNAME" in content and "GIT_PASSWORD" in content
        assert "Username*" in content
    finally:
        auth.cleanup()
    assert not os.path.exists(auth.env["GIT_ASKPASS"])  # cleanup borró el temporal


def test_pat_respects_explicit_username_and_provider_defaults() -> None:
    assert build_git_auth_env("pat", username="me", token="t").env["GIT_USERNAME"] == "me"
    assert build_git_auth_env("pat", provider="gitlab", token="t").env["GIT_USERNAME"] == "oauth2"


def test_ssh_writes_keyfile_and_sets_ssh_command() -> None:
    # Clave dummy (NO real) — evita el literal del header de clave OpenSSH para no
    # disparar el hook detect-private-key. git_auth no valida el formato; escribe
    # el contenido tal cual.
    key = "DUMMY-ssh-private-key-for-tests\nsecond-line"
    auth = build_git_auth_env("ssh", ssh_key=key)
    try:
        cmd = auth.env["GIT_SSH_COMMAND"]
        assert cmd.startswith("ssh -i ")
        assert "IdentitiesOnly=yes" in cmd and "StrictHostKeyChecking=accept-new" in cmd
        keyfile = cmd.split("-i ", 1)[1].split(" ", 1)[0]
        assert os.path.exists(keyfile)
        with open(keyfile, encoding="utf-8") as f:
            assert f.read().startswith("DUMMY-ssh-private-key")
    finally:
        auth.cleanup()
        assert not os.path.exists(keyfile)


def test_pat_without_token_is_noop() -> None:
    assert build_git_auth_env("pat", token=None).env == {}


def test_none_mode_is_noop() -> None:
    auth = build_git_auth_env("none")
    assert auth.env == {}
    auth.cleanup()  # no debe romper


def test_unknown_mode_is_noop() -> None:
    assert build_git_auth_env(None, token="x").env == {}
