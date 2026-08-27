# installer — temporary self-destructing install wizard

This app is the **temporary** installer for the agentic platform (Plan 15,
Fase A). It is a Next.js wizard UI + a minimal FastAPI backend, run as its own
bootstrap container that is **destroyed after install completes**. It is **not**
part of the runtime Docker Compose stack.

## Read this before you design anything on top of it

> ⚠️ **The HTTP wizard is a facade. It does not install anything.**
>
> `POST /api/install/stream` runs against a `FakeStepExecutor`
> ([`main.py`](backend/src/installer_backend/main.py), `_default_step_executor`).
> It walks the nine steps, streams believable SSE progress, and then reveals a
> set of credentials and five Vault unseal keys that are **generated with
> `secrets.token_urlsafe` and thrown away** — `build_install_credentials()` says
> so in its own docstring. Nothing was provisioned; no Vault was initialised; no
> admin user exists. **The credentials the wizard shows you are not real and open
> nothing.** Write them down and you have written down noise.
>
> The **real** install path is the CLI — `scripts/install.sh` with a `--config`
> profile, which lands in `installer_backend.cli.run_install`. It wires the real
> bindings by default and **aborts with exit code 4 (`PROVISION`)** if it ever
> detects a simulation seam without `--dry-run`
> (`cli._assert_real_install_seams`). There is no such thing as a silent fake
> install on that path — which is exactly the guarantee the wizard lacks.
>
> Wiring the wizard to the real executor (per-request `compose_dir` / `cfg` /
> `secrets` plumbing, plus a simulation guard on the reveal) is a documented
> follow-up owned by the installer UI (prod-09). Until that lands, treat the
> wizard as a UX prototype: it is good for reviewing the flow, the copy and the
> GPU-detection screen, and for nothing else.

This README used to claim the exact opposite of the box above: that the installer
provisioned a real host — Docker, `pg_*`, Vault, writing under
`/data/agent-platform`. It was wrong for the wizard, and it was the expensive
kind of wrong (the sentence is quoted verbatim in ADR 0161; it is paraphrased
here on purpose, so that a text guard can forbid the claim without tripping over
its own obituary). A runbook is
read by someone who is already installing, but a README is read by someone who is
**designing** — the packaging, the no-clone install path, the budget. That
sentence was quoted as a premise in
[ADR 0161](../../docs/05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md)
as the reason this correction exists.

## Known breakage on the real path too: the relative paths

Even the CLI cannot finish on a clean machine today, and the reason is worth
understanding before you debug the symptom instead of the cause.

The generated `docker-compose.yml` is **not** written into the repository: it is
written into the data root (`cli.py` → `compose_dir = config.storage.data_root`,
`/data/agent-platform` by default), and every `docker compose` call runs with
`cwd=compose_dir`. So each `./something` bind in that file resolves against
`/data/agent-platform/…`, where there is no checkout. **Cloning the repository
does not fix it** — that is the counter-intuitive half, and the reason nobody
deduced it on their own.

Six of the seven relative-path families the generated compose asks for are never
written by the installer (`./egress-proxy`, `./registry-proxy`,
`./postgres/init`, `./vault/config.hcl`, `./docker/seccomp`, `./monitoring/**`);
only `./caddy/Caddyfile` is. Two of them collide with data binds the installer
_does_ create, and that is where the failure stops announcing itself: Docker
materialises the missing host side of a bind as an **empty directory**, so
`./postgres/init` lands _inside_ PGDATA — `initdb` then finds a non-empty data
directory and the real init scripts (pgvector, service roles) never run. What you
see is a Postgres that reports `healthy` and has no `pgvector`.

The full measurement, with the file:line for every path, is in
[ADR 0161](../../docs/05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md)
§"La avería que no estaba escrita". **A repair is in progress** and its executable
guard derives both sets — the paths the compose asks for and the paths the
install actually produces — from the code rather than from a hand-written list,
because a hand-written list ages the moment someone adds a mount. No date is
promised here; check the ADR's status for where it stands.

## Layout

```
apps/installer/
├── app/                         # Next.js App Router — the wizard shell
│   ├── page.tsx                 #   entry → <WizardShell/>
│   ├── wizard-shell.tsx         #   stepper + panel + back/next navigation
│   ├── stepper.tsx              #   9-step vertical stepper
│   └── step-panel.tsx           #   per-step body (welcome filled; 2–9 stubs)
├── lib/
│   ├── wizard.ts                # 9-step flow definition (mirrors backend)
│   └── use-wizard.ts            # client-side state machine (pure navigation)
├── e2e/installer-wizard.spec.ts # Playwright shell spec (written, not run)
├── backend/                     # FastAPI bootstrap backend
│   ├── pyproject.toml           #   package: installer_backend
│   └── src/installer_backend/
│       ├── wizard.py            #   pure 9-step state machine
│       ├── seams.py             #   injectable host seams (prereqs/install/lifecycle)
│       ├── cli.py               #   the REAL install path (real bindings by default)
│       └── main.py              #   FastAPI app — SIMULATED wizard (FakeStepExecutor)
├── Dockerfile                   # UI image (Next standalone)
├── backend/Dockerfile           # backend image (FastAPI)
└── docker-compose.installer.yml # bootstrap compose (runs ONLY the installer)
```

## The 9-step flow

`Bienvenida → Config básica → Recursos/GPU → Almacenamiento → Providers LLM →
Tenant inicial → Resumen → Instalación → Listo`

Phase A (task_15_01) ships the **shell**: ordering, titles and the forward/back
state machine. Later tasks fill the steps:

| Step(s)                                | Task                 | Real over HTTP?               |
| -------------------------------------- | -------------------- | ----------------------------- |
| Prereq validation (step 1 / Recursos)  | 15_02                | no — `StubPrereqChecker`      |
| Capture forms (basics/resources/...)   | 15_03                | yes — config capture is real  |
| Summary preview + confirm              | 15_04                | yes                           |
| Install progress + live logs           | 15_05                | **no — `FakeStepExecutor`**   |
| One-shot credentials + self-destruct   | 15_06                | **no — throwaway secrets**    |
| Config generators (compose/.env/Vault) | 15_07–15_09 (Fase B) | used by the **CLI**, not HTTP |

The generators of the last row are real, tested and reachable — the CLI calls
them. What is missing is the wiring that lets the _HTTP_ wizard call them too.

## Why seams + mocks

A real install touches Docker, `pg_*`, Vault and `/data/agent-platform`, none of
which runs in CI. Every host-touching action lives behind a Protocol in
[`backend/src/installer_backend/seams.py`](backend/src/installer_backend/seams.py)
(`PrereqChecker`, `InstallRunner`, `InstallerLifecycle`) and is faked in tests.

The seams are the right design; the defect is only _which_ implementation each
entry point binds. The CLI binds the real ones and refuses to run on a fake; the
HTTP app binds the stubs and refuses nothing. Verifying an actual install is a
**human** test in the plan
([`docs/03-guides/human-tests/15-instalador-produccion.md`](../../docs/03-guides/human-tests/15-instalador-produccion.md)).

Security note: on the real path, generated credentials and Vault unseal keys are
shown **once** and never persisted in plaintext nor logged. On the HTTP wizard the
same ceremony runs over values that mean nothing.

## Develop

```bash
# frontend
cd apps/installer && npm install && npm run dev    # http://localhost:3100

# backend
python -m installer_backend                        # http://localhost:8080/healthz
```

## Test

```bash
# backend (pytest, no Docker host needed — seams are mocked)
pytest tests/unit/test_installer_backend.py -v

# frontend
cd apps/installer && npm run typecheck && npm run lint && npm run build

# e2e (WRITTEN, not run in Phase A — pending human verification)
cd apps/installer && npm run e2e:install && npm run e2e
```

## Run the bootstrap container

Only to review the wizard's UX. It will **not** install the platform.

```bash
docker compose -f apps/installer/docker-compose.installer.yml up -d --build
# open http://localhost:3100 ; tear down when you are done:
docker compose -f apps/installer/docker-compose.installer.yml down
```

Note that `--build` is not optional today: both services are declared with
`build:`, so this compose file **requires a clone of the repository**. There is no
published installer image — the release workflow publishes six application images
and none of them is this one. That is the opening fact of
[ADR 0161](../../docs/05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md),
which is still `proposed`: whether an install-without-clone path exists as a
supported product is an open operator decision, not something to infer from this
file.

To actually install, use the CLI — the path documented in
[`docs/06-runbooks/01-installation-from-scratch.md`](../../docs/06-runbooks/01-installation-from-scratch.md):

```bash
cp scripts/install-profiles/recommended.yaml install.yaml
# edit install.yaml (domain, providers, sizing, initial tenant…)
./scripts/install.sh --config install.yaml
```
