# Stack Tecnológico Detallado

## Backend

| Capa | Tecnología | Versión | Notas |
|------|-----------|---------|-------|
| Lenguaje | Python | 3.12+ | Async-first |
| Framework HTTP | FastAPI | 0.115+ | OpenAPI auto, validación Pydantic |
| Servidor ASGI | Uvicorn | 0.32+ | Behind nginx en producción |
| ORM | SQLAlchemy | 2.x | async, type hints |
| Migraciones | Alembic | 1.13+ | Reversibles obligatorio |
| Validación | Pydantic | 2.x | v2 con strict mode |
| BD relacional | PostgreSQL | 16 | + extensions pgvector y pg_trgm |
| BD vectorial | pgvector | 0.7+ | HNSW index para embeddings |
| Cache + broker | Redis | 7 | Streams + Pub/Sub + Cache |
| Cola de tareas | Celery | 5.4+ | Con acks_late=true |
| Orquestación agentes | LangGraph | latest | Grafos de estado para agent loop |
| LLM abstracción | LiteLLM | latest | Gateway 100+ providers |
| LLM (suscripción) | claude-agent-sdk | latest | Python SDK oficial |
| Object storage | MinIO | latest | S3-compatible |
| Secretos | HashiCorp Vault | 1.18+ | KV v2 |
| Antivirus | ClamAV | latest | Para uploads a KBs |
| Ingestión docs | Docling | latest | IBM, vía docling-serve |

## Frontend

| Capa | Tecnología | Versión |
|------|-----------|---------|
| Framework | Next.js | 14+ (App Router) |
| UI library | React | 18+ |
| Estado servidor | TanStack Query | v5 |
| Estado cliente | Zustand | latest |
| Estilos | Tailwind CSS | 3.4+ |
| Componentes | shadcn/ui | latest |
| Markdown | react-markdown + remark/rehype | latest |
| Diagramas | Mermaid | latest |
| Terminal web | xterm.js o ttyd | latest |
| Sintaxis | highlight.js o Shiki | latest |
| Tiempo real | WebSocket nativo + EventSource | - |

## Workers y Runtimes

| Worker | Imagen base | Recursos default |
|--------|-------------|------------------|
| worker-default | python:3.12-slim + Celery | 1 CPU / 2 GB RAM |
| worker-heavy | python:3.12-slim + Celery | 4 CPU / 8 GB RAM |
| worker-gpu | nvidia/cuda:12.6 + Python | 2 CPU / 8 GB RAM + GPU |
| worker-ingestion | python:3.12 + Celery | 2 CPU / 4 GB RAM |
| worker-test | python:3.12-slim + Docker SDK | 1 CPU / 2 GB RAM |
| worker-review | python:3.12-slim + Docker SDK | 1 CPU / 2 GB RAM |

## Runtime Templates (sección 14)

| Runtime | Imagen base | Test runner | Formato salida |
|---------|-------------|-------------|----------------|
| python-pytest | python:3.12-slim + pytest + pytest-cov | pytest | junit_xml + json |
| node-jest | node:20-alpine + jest | jest | jest_json |
| node-vitest | node:20-alpine + vitest | vitest | junit_xml + json |
| node-playwright | mcr.microsoft.com/playwright:focal | playwright test | playwright_json |
| php-phpunit | php:8.3-cli + phpunit + composer | phpunit | junit_xml + tap |
| php-pest | php:8.3-cli + pest + composer | pest | junit_xml + tap |
| go-test | golang:1.23 + gotestsum | go test / gotestsum | junit_xml + json |
| java-maven | maven:3.9-eclipse-temurin-21 | mvn test | surefire_xml |
| java-gradle | gradle:8-jdk21 | gradle test | junit_xml |
| ruby-rspec | ruby:3.3-slim + rspec + bundler | rspec | junit_xml + tap |
| rust-cargo | rust:1.80-slim + nextest | cargo nextest | junit_xml + json |
| dotnet-test | mcr.microsoft.com/dotnet/sdk:8.0 + xunit | dotnet test | trx + junit_xml |
| generic-shell | alpine:latest + bash | comando arbitrario | exit_code |
| generic-http | alpine + curl + jq | scripts curl | http_assert_json |

## Observabilidad

| Capa | Tecnología |
|------|-----------|
| Logs | Loki + Promtail |
| Métricas | Prometheus + node-exporter + cAdvisor |
| Tracing | OpenTelemetry + Tempo o Jaeger |
| Dashboards | Grafana |
| Alerting | Alertmanager |

## Tooling de Desarrollo

| Herramienta | Uso |
|-------------|-----|
| black + ruff | Formato + lint Python |
| mypy | Type checking Python |
| prettier + eslint | Formato + lint TypeScript |
| pytest | Tests Python |
| vitest | Tests TypeScript |
| playwright | E2E tests del frontend |
| testcontainers | Tests de integración con DB efímera |
| pre-commit | Hooks pre-commit |
| commitizen | Conventional Commits |

## CI/CD

| Capa | Tecnología |
|------|-----------|
| CI | GitHub Actions o GitLab CI |
| Security scanning | Trivy (imágenes) + Bandit (Python) + Snyk/OSV (deps) |
| Container registry | Registry interno del operador |
| Versionado | semver + git tags |

## Auth y Seguridad

| Capa | Tecnología |
|------|-----------|
| JWT signing | jose o pyjwt |
| Password hashing | argon2-cffi |
| OAuth/OIDC | authlib |
| SAML | python3-saml |
| MFA TOTP | pyotp |
| MFA WebAuthn | webauthn (py_webauthn) |
| RBAC | Casbin |
| RLS | PostgreSQL nativo |

## Notificaciones (sección 17)

| Canal | Librería |
|-------|----------|
| Telegram | python-telegram-bot |
| WhatsApp | WhatsApp Cloud API client o Twilio SDK |
| Email | aiosmtplib o sendgrid/anymail |
| Slack | slack-bolt |
| MS Teams | requests + Adaptive Cards |
| Discord | discord.py o webhook directo |
| SMS | Twilio SDK |
| Webhooks | httpx + jinja2 (plantillas) + HMAC nativo |

## Versionado de Componentes

Política: cada servicio del stack es una imagen Docker con tag semver. El docker-compose.yml referencia tags explícitos, nunca `latest` en producción.
