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

### What changed on 2026-08-28: the simulation now declares itself

Everything in the box above was already true, and was already written down — in
a Python docstring, a YAML comment, this README and a runbook. **None of it
reached the screen.** A `grep -iE 'simulaci|simulation|demo|fake'` over `app/`
and `lib/` returned zero hits while the wizard was telling operators
«Instalación completada. La plataforma está instalada» and handing them five
Vault unseal keys under «save these now, there is no way to recover them».

Three ways out were costed. Wiring the real executor is **not** one of them: ADR
0161 signed that the installer container _generates and does not provision_, and
without the Docker socket four of the five pipeline steps are impossible from
inside — that road is 4-7 days **and a new ADR**. Removing the wizard costs 2-3
days and throws away the half that is real (config capture + validation, steps
2-7) plus two supply-chain guards that need the npm surface to exist. So:

- **The two lying endpoints are off by default.** `/api/install/stream` and
  `/api/finalize/reveal` answer `501` — naming the CLI — unless
  `INSTALLER_ALLOW_SIMULATION` is set. Steps 1-7 keep serving, because they are
  real.
- **When the simulation does run, it says so on screen**: a permanent red banner
  in the shell, a blocking dialog before the «Instalar» button that you have to
  tick through, an explicit mark on the prerequisite list (those checks are a
  stub and measure nothing about your machine), and a step-9 header that reads
  «Simulación terminada — no se ha instalado nada» instead of announcing an
  install. `simulated: true` also travels in the response bodies, for clients
  that are not this browser.
- **The stream no longer accepts secrets.** It used to receive
  `storage.minio_secret_key` and the provider `oauth_token` / `api_key` in the
  clear while three separate docstrings claimed it did not. The browser strips
  them and the backend rejects them with a `400` that names the field and never
  the value.

`apps/installer/docker-compose.installer.yml` sets the variable explicitly and
publishes on `127.0.0.1` only — the backend has no authentication of any kind.

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

## Known breakage on the real path: the relative paths (fixed — read it anyway)

Until PR #124 the CLI could not finish on a clean machine either, and the reason
is worth understanding before you debug the symptom instead of the cause. It is
also why the installer package looks the way it does.

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
§"La avería que no estaba escrita".

**This one is fixed.** The auxiliaries now travel _inside_ the installer package
(`installer_backend.stack_assets` — 23 files: Postgres init, the Vault config,
both tinyproxy contexts, seccomp, monitoring), and the install writes them to
`{data_root}/stack/` before the generated compose mounts them, so there is
nothing left to resolve against a checkout that is not there. ADR 0161 answers
its own question 4 with «Ya hecho». The executable guard
([`tests/unit/test_installer_ships_stack_assets.py`](../../tests/unit/test_installer_ships_stack_assets.py))
derives both sets — the paths the compose asks for and the paths the install
actually produces — from the code rather than from a hand-written list, because a
hand-written list ages the moment someone adds a mount.

The breakage is written down here in the past tense on purpose: it is the reason
the packaging looks the way it does, and the failure mode it produced (a Postgres
reporting `healthy` with no `pgvector`, because Docker materialises a missing
bind source as an empty directory) is worth recognising if it ever comes back.

## Layout

```
apps/installer/
├── app/                         # Next.js App Router — the wizard shell
│   ├── page.tsx                 #   entry → <WizardShell/>
│   ├── wizard-shell.tsx         #   stepper + panel + back/next navigation
│   ├── stepper.tsx              #   9-step vertical stepper
│   ├── simulation-notice.tsx    #   the «this installs nothing» banner + gate
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
│       └── main.py              #   FastAPI app — SIMULATED wizard (FakeStepExecutor),
│                                #     gated behind INSTALLER_ALLOW_SIMULATION
├── Dockerfile                   # UI image (Next standalone)
├── backend/Dockerfile           # backend image (FastAPI)
└── docker-compose.installer.yml # bootstrap compose (runs ONLY the installer)
```

## The 9-step flow

`Bienvenida → Config básica → Recursos/GPU → Almacenamiento → Providers LLM →
Tenant inicial → Resumen → Instalación → Listo`

Phase A (task_15_01) ships the **shell**: ordering, titles and the forward/back
state machine. Later tasks fill the steps:

| Step(s)                                | Task                 | Real over HTTP?                                         |
| -------------------------------------- | -------------------- | ------------------------------------------------------- |
| Prereq validation (step 1 / Recursos)  | 15_02                | no — `StubPrereqChecker`                                |
| Capture forms (basics/resources/...)   | 15_03                | yes — config capture is real                            |
| Summary preview + confirm              | 15_04                | yes                                                     |
| Install progress + live logs           | 15_05                | **no — `FakeStepExecutor`**, 501 unless the flag is set |
| One-shot credentials + self-destruct   | 15_06                | **no — throwaway secrets**, 501 unless the flag is set  |
| Config generators (compose/.env/Vault) | 15_07–15_09 (Fase B) | used by the **CLI**, not HTTP                           |

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
# open http://127.0.0.1:3100 ; tear down when you are done:
docker compose -f apps/installer/docker-compose.installer.yml down
```

Both ports are bound to loopback on purpose: this backend has no authentication
and its CORS is `allow_origins=["*"]`. That compose also sets
`INSTALLER_ALLOW_SIMULATION=1` explicitly — without it the wizard refuses to fake
an install, which is the right default for the published image and useless for
the one thing this compose is for.

Note that `--build` is not optional here: both services are declared with
`build:`, so this compose file **requires a clone of the repository**. The
_published_ image is a different thing: since
[ADR 0161](../../docs/05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md)
was signed on 2026-08-27, `release-images.yml` publishes a seventh image —
`ghcr.io/<owner>/installer` — built from `backend/Dockerfile`, whose `ENTRYPOINT`
is the **CLI**, not this wizard. Packaging the wizard would be publishing the
facade. The no-clone install path runs that image via
[`docker/bootstrap/docker-compose.generate.yml`](../../docker/bootstrap/docker-compose.generate.yml);
read that file before you run it, which is the point of it being a file and not a
`curl | bash`.

To actually install, use the CLI — the path documented in
[`docs/06-runbooks/01-installation-from-scratch.md`](../../docs/06-runbooks/01-installation-from-scratch.md):

```bash
cp scripts/install-profiles/recommended.yaml install.yaml
# edit install.yaml (domain, providers, sizing, initial tenant…)
./scripts/install.sh --config install.yaml
```
