# installer — temporary self-destructing install wizard

This app is the **temporary** installer for the agentic platform (Plan 15,
Fase A). It is a Next.js wizard UI + a minimal FastAPI backend, run as its own
bootstrap container that is **destroyed after install completes**. It is **not**
part of the runtime Docker Compose stack.

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
│       └── main.py              #   FastAPI app (/healthz + /api/wizard/* + /api/prereqs)
├── Dockerfile                   # UI image (Next standalone)
├── backend/Dockerfile           # backend image (FastAPI)
└── docker-compose.installer.yml # bootstrap compose (runs ONLY the installer)
```

## The 9-step flow

`Bienvenida → Config básica → Recursos/GPU → Almacenamiento → Providers LLM →
Tenant inicial → Resumen → Instalación → Listo`

Phase A (task_15_01) ships the **shell**: ordering, titles and the forward/back
state machine. Later tasks fill the steps:

| Step(s)                                | Task                 |
| -------------------------------------- | -------------------- |
| Prereq validation (step 1 / Recursos)  | 15_02                |
| Capture forms (basics/resources/...)   | 15_03                |
| Summary preview + confirm              | 15_04                |
| Install progress + live logs           | 15_05                |
| One-shot credentials + self-destruct   | 15_06                |
| Config generators (compose/.env/Vault) | 15_07–15_09 (Fase B) |

## Why seams + mocks

The installer actually provisions a real stack (Docker, `pg_*`, Vault) and
writes under `/data/agent-platform` — none of which runs in CI. Every
host-touching action lives behind a Protocol in `backend/.../seams.py`
(`PrereqChecker`, `InstallRunner`, `InstallerLifecycle`) and is faked in tests.
The real install / uninstall is a **human** test in the plan.

Security: generated credentials and Vault unseal keys are shown **once** and
never persisted in plaintext nor logged.

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

```bash
docker compose -f apps/installer/docker-compose.installer.yml up -d --build
# open http://localhost:3100 ; tear down after install:
docker compose -f apps/installer/docker-compose.installer.yml down
```
