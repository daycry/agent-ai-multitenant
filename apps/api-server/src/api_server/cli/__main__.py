"""CLI entry point: ``python -m api_server.cli <command>``.

Commands
--------
``reencrypt-secrets``
    Re-encrypt every at-rest Fernet ciphertext onto the head key of its ring —
    step 2 of the rotation in ``docs/06-runbooks/05-key-rotation.md``.

    ``--dry-run`` counts what WOULD move and writes nothing. Run it first: its
    output is what tells you whether retiring the previous key is safe yet.

``audit-approval-policies``
    El informe previo del ADR 0153: proyecto a proyecto, qué categorías escribiría
    la migración 0133 en su ``human_approval_policy`` y cuáles quedan gateadas.
    SOLO LECTURA — abre la transacción con ``SET TRANSACTION READ ONLY``, así que
    se puede correr en producción sin riesgo y sin permisos de escritura.

Exit codes are part of the contract (an operator may wrap this in a script):

    0   the run succeeded. Under ``--dry-run`` that includes "there is work to do".
    1   the configuration is wrong (unknown table, empty key ring, ...) or the
        run failed. Nothing was written, or the write was rolled back.
    2   the run succeeded but something needs a human BEFORE the corresponding
        write can proceed. Distinct from 0 so a wrapper cannot mistake "did
        everything I could" for "did everything":
          * ``reencrypt-secrets``  — some rows are UNREADABLE (no key decrypts them);
          * ``audit-approval-policies`` — hay políticas incoherentes, así que la
            migración 0133 ABORTARÍA. Este comando la pre-vuela.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

# Import the ORM aggregator first so every mapper is registered before a session
# is created — the same reason `api_server.seeds.__main__` does it.
from api_server.db import models as _models  # noqa: F401
from api_server.logging import configure_logging

_EXIT_OK = 0
_EXIT_ERROR = 1
#: "Salió bien, pero hay algo que un humano tiene que mirar antes de escribir."
_EXIT_NEEDS_A_HUMAN = 2
_EXIT_UNREADABLE_ROWS = _EXIT_NEEDS_A_HUMAN


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api_server.cli",
        description="Platform-global administrative commands for the api-server.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    reencrypt = subparsers.add_parser(
        "reencrypt-secrets",
        help="Re-encrypt at-rest secrets onto the head key of each key ring.",
        description=(
            "Step 2 of an at-rest key rotation. Decrypts every stored ciphertext "
            "with the whole ring and re-encrypts it with the FIRST key, so the "
            "previous key can then be retired. Idempotent."
        ),
    )
    reencrypt.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change and write NOTHING.",
    )
    reencrypt.add_argument(
        "--tables",
        nargs="+",
        metavar="TABLE",
        help="Restrict to these tables (default: all). An unknown name is an error.",
    )
    reencrypt.add_argument(
        "--families",
        nargs="+",
        metavar="FAMILY",
        help="Restrict to these key families: sso, mfa, notification, incoming_webhook.",
    )
    reencrypt.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Rows per transaction (default: 200).",
    )

    audit = subparsers.add_parser(
        "audit-approval-policies",
        help="Informe previo del ADR 0153: qué completaría la migración 0133.",
        description=(
            "Recorre TODOS los proyectos y lista, uno a uno, qué categorías de su "
            "política de aprobación se escribirían (las que hoy son implícitas) y "
            "cuáles quedan gateadas. NO escribe nada: la transacción se marca "
            "READ ONLY. Sale con código 2 si encuentra políticas incoherentes, que "
            "es lo que haría abortar a la migración."
        ),
    )
    audit.add_argument(
        "--all",
        action="store_true",
        help="Listar también los proyectos cuya política ya está completa.",
    )
    return parser


async def _run_reencrypt(args: argparse.Namespace) -> int:
    from api_server.cli.reencrypt_secrets import reencrypt_secrets
    from api_server.config import get_settings
    from api_server.db.session import get_admin_sessionmaker

    settings = get_settings()
    # The ADMIN (BYPASSRLS, no-DDL `service_user`) engine: the command must see
    # every tenant's rows. A tenant-bound session would migrate a subset and
    # report success — and the operator would then retire a key that half the
    # platform still needs.
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        report = await reencrypt_secrets(
            session,
            settings=settings,
            dry_run=args.dry_run,
            tables=args.tables,
            families=args.families,
            batch_size=args.batch_size,
        )
        if args.dry_run:
            # Belt and braces: the engine never writes under --dry-run, but an
            # explicit rollback means a future bug cannot leak a write through.
            await session.rollback()

    print(report.render())  # - this is a CLI; stdout IS the interface
    return _EXIT_UNREADABLE_ROWS if report.unreadable else _EXIT_OK


async def _run_approval_policy_audit(args: argparse.Namespace) -> int:
    from api_server.cli.approval_policy_audit import audit_approval_policies
    from api_server.db.session import get_admin_sessionmaker

    # El engine ADMIN (BYPASSRLS) por la misma razón que `reencrypt-secrets`:
    # el informe tiene que ver los proyectos de TODOS los tenants. Una sesión
    # atada a un tenant informaría de un subconjunto y el operador firmaría
    # creyendo que ha visto el total. La transacción va READ ONLY (lo pone
    # `audit_approval_policies`), así que el permiso de escritura del rol no se
    # usa aquí ni por accidente.
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        report = await audit_approval_policies(session)
        await session.rollback()

    print(report.render(show_complete=args.all))  # - this is a CLI; stdout IS the interface
    return _EXIT_NEEDS_A_HUMAN if report.would_abort else _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    configure_logging(service="api-server-cli")
    log = structlog.get_logger("api_server.cli")
    args = _build_parser().parse_args(argv)

    if args.command in ("reencrypt-secrets", "audit-approval-policies"):
        runner = (
            _run_reencrypt if args.command == "reencrypt-secrets" else _run_approval_policy_audit
        )
        try:
            return asyncio.run(runner(args))
        except ValueError as exc:
            # Configuration errors (unknown table, empty key ring): the message is
            # the whole point, and it never carries a key value.
            log.error("api_server.cli.invalid_arguments", error=str(exc))
            print(f"error: {exc}", file=sys.stderr)
            return _EXIT_ERROR

    raise AssertionError(f"unhandled command {args.command!r}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
