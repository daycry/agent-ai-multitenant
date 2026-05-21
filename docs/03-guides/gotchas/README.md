# Gotchas / troubleshooting

Notas sobre **bugs no-obvios** del toolchain que ya nos han pasado y
cómo se resolvieron. Antes de inventar un fix nuevo para un error de
Docker, Compose, mypy, pre-commit, asyncpg, OpenTelemetry o
git/Windows, **busca aquí primero**: hay muchas posibilidades de que
el problema ya esté documentado.

> Estas notas NO sustituyen a un ADR. Un ADR documenta una **decisión**
> arquitectónica; una gotcha documenta una **trampa** del toolchain.

## Cómo añadir una nueva

1. Crea un archivo nuevo con slug `<area>-<short-slug>.md` (e.g.
   `docker-compose-volumes-merge.md`, `windows-asyncio-proactor.md`).
2. Usa el front-matter:

   ```yaml
   ---
   title: ... # frase corta
   area: docker | python | windows | pre-commit | otel | postgres | ...
   encountered: 2026-05-21 # fecha
   stack: docker compose v2.x, vault 1.17, ... # versiones afectadas
   ---
   ```

3. Cuatro secciones obligatorias:
   - **Síntoma** — qué se ve.
   - **Causa raíz** — por qué pasa.
   - **Fix** — qué se cambió.
   - **Cómo verificar el fix** — comando o aserción que valida la solución.

4. Cuando una versión más reciente del tooling vuelve la nota
   obsoleta, **edita la nota** en vez de borrarla: añade una nota
   "obsoleto desde X.Y" arriba para que el lector lo vea.

## Índice por área

### docker / docker-compose

- [docker-compose-volumes-merge.md](./docker-compose-volumes-merge.md)
  — `volumes:` se mergea entre overrides, no se reemplaza.
- [vault-dev-mode-port-conflict.md](./vault-dev-mode-port-conflict.md)
  — `-dev` + config.hcl mount → EADDRINUSE 8200.
- [vault-entrypoint-config-flag.md](./vault-entrypoint-config-flag.md)
  — pasar `-config=` al `command:` choca con el entrypoint.

### postgres / asyncpg / sqlalchemy

- [postgres-port-clash-with-laragon.md](./postgres-port-clash-with-laragon.md)
  — host 5432 lo ocupa Laragon; usamos 15432.
- [postgres-roles-bypassrls.md](./postgres-roles-bypassrls.md)
  — `migrations_user` necesita `BYPASSRLS`, `app_user` no.
- [postgres-alter-default-privileges-per-db.md](./postgres-alter-default-privileges-per-db.md)
  — los privilegios por defecto no viajan a una BD nueva.
- [asyncpg-set-local-no-bind-params.md](./asyncpg-set-local-no-bind-params.md)
  — `SET LOCAL x = $1` falla; usar `set_config('x', $1, true)`.
- [asyncpg-no-multistatement.md](./asyncpg-no-multistatement.md)
  — `op.execute` no acepta múltiples sentencias separadas por `;`.

### pre-commit / mypy / ruff

- [pre-commit-python-version-pin.md](./pre-commit-python-version-pin.md)
  — pin `python3.12` rompe en host 3.13.
- [pre-commit-checkyaml-compose-tags.md](./pre-commit-checkyaml-compose-tags.md)
  — `check-yaml` no entiende `!reset` / `!override`.
- [mypy-local-package-imports.md](./mypy-local-package-imports.md)
  — el hook venv no ve los `apps/<x>` editables; usar `additional_dependencies`
  o `exclude:`.
- [precommit-mixed-line-ending-vs-gitattributes.md](./precommit-mixed-line-ending-vs-gitattributes.md)
  — `mixed-line-ending --fix=lf` no respeta `.gitattributes`; hay que
  excluir `.ps1` / `.cmd` / `.bat` del hook.

### opentelemetry

- [otel-console-exporter-pytest-stdout.md](./otel-console-exporter-pytest-stdout.md)
  — `ConsoleSpanExporter` revienta cuando pytest captura stdout.
- [otel-global-provider-tests.md](./otel-global-provider-tests.md)
  — el provider global no se puede reemplazar; tests añaden un span processor.

### windows

- [windows-asyncio-engine-dispose.md](./windows-asyncio-engine-dispose.md)
  — `asyncio.run(engine.dispose())` en teardown crashea el proactor.
- [windows-git-crlf-vs-hooks.md](./windows-git-crlf-vs-hooks.md)
  — `core.autocrlf` pelea con `mixed-line-ending`; arreglado con
  `.gitattributes`.

### next.js / typescript

- [nextjs-eslint-root-inherit.md](./nextjs-eslint-root-inherit.md)
  — `.eslintrc.json` hereda del root y exige plugins TS ausentes;
  `root: true`.

### ci / github actions

- [ci-github-actions-node-deprecation.md](./ci-github-actions-node-deprecation.md)
  — Node 20 deprecado; `actions/checkout@v4 → v5`, etc.
