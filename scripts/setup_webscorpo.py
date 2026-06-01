"""Seed reproducible del equipo WebScorpo (plan ``demo-webscorpo-team-kb``).

Materializa en la plataforma el proyecto real **WebScorpo** (la app
PHP/CodeIgniter 4 en ``C:/laragon/www/webscorpo``, solo-lectura) como un
equipo usable: un tenant/org **Mediapro**, un **equipo WebScorpo** con
**10 agentes** especializados en el stack (roles del análisis §7), el
**proyecto webscorpo** con su config de comandos/runtime PHP (campos del
Plan 06.16: ``allowed_commands`` + ``default_runtime_template``) y las
**asignaciones de tools por agente** vía la junction ``agent_tools``
(Plan 06.15: ``shell_exec`` + file/git a todos; ``run_*`` a backend/dba/
qa/devops; ``http_get`` a auth-security/devops; etc.).

Todo el contenido se deriva del análisis (``C:/tmp/webscorpo-analysis.md``)
— no se inventan hechos. El seed **no toca** el proyecto webscorpo en disco.

Diseño (mismo patrón que ``api_server.seeds`` + ``scripts/setup_demo_*.py``):

  * **Idempotente** por identidad estable: cada org/equipo/agente/proyecto
    tiene un UUID derivado por ``uuid5(NAMESPACE, slug)``, así re-ejecutar
    es un upsert real, nunca duplica. Las asignaciones de tools se
    re-concilian (upsert + borrado de filas obsoletas).
  * **Tenant-scoped**: todas las filas llevan el ``tenant_id`` de Mediapro
    (los agentes son ``scope=global_tenant_template`` — agentes-plantilla
    propios del tenant que pueden ser miembros de equipo y NO requieren
    ``project_id``, respetando el invariante ``ck_agents_scope_project``).
  * **Reutiliza** el catálogo built-in de tools (``seed_builtin_tools``):
    el seed primero garantiza que existan TODAS las tools built-in
    (incluida ``shell_exec`` del Plan 06.16) antes de asignarlas.

Las KBs (team-shared + por-agente) y la ingesta del corpus se añaden en
``task_demo_ws_03`` reutilizando este mismo módulo.

Uso (desde la raíz del repo):

    .\\.venv\\Scripts\\python.exe scripts\\setup_webscorpo.py
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid5

# La consola de Windows usa cp1252; forzamos UTF-8 para no romper al
# imprimir acentos / caracteres de caja al ejecutar el seed a mano.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# Permite ejecutar el script directamente (``python scripts/setup_webscorpo.py``)
# además de importarlo como ``scripts.setup_webscorpo`` desde los tests: añade
# el dir del script a ``sys.path`` para que ``_demo_common`` resuelva en ambos.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# =============================================================================
# Identidad estable (uuid5) — re-seedear nunca duplica
# =============================================================================
# Namespace propio del seed WebScorpo (distinto de los namespaces de los
# built-ins de la plataforma para no colisionar). Cualquier id del seed se
# deriva como uuid5(WEBSCORPO_NAMESPACE, "<kind>:<slug>").
WEBSCORPO_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-0000005c0090")

TENANT_SLUG = "mediapro"
TENANT_NAME = "Mediapro"
TEAM_SLUG = "webscorpo"
TEAM_NAME = "WebScorpo"
PROJECT_SLUG = "webscorpo"
PROJECT_NAME = "webscorpo"

# Plan 06.16: la allowlist deny-by-default de comandos del stack PHP/CI4 que
# `shell_exec` puede correr en este proyecto + el runtime template del stack.
# Derivado del toolchain del análisis (§6.2 composer scripts, §2 tests).
PROJECT_ALLOWED_COMMANDS: tuple[str, ...] = (
    "php",
    "composer",
    "vendor/bin/phpunit",
    "vendor/bin/pest",
    "vendor/bin/infection",
    "npm",
    "npx",
)
PROJECT_RUNTIME_TEMPLATE = "php-phpunit"


def _id(kind: str, slug: str) -> UUID:
    """UUID estable de una entidad del seed por (kind, slug)."""
    return uuid5(WEBSCORPO_NAMESPACE, f"{kind}:{slug}")


def tenant_id() -> UUID:
    return _id("org", TENANT_SLUG)


def team_id() -> UUID:
    return _id("team", TEAM_SLUG)


def project_id() -> UUID:
    return _id("project", PROJECT_SLUG)


def agent_id(slug: str) -> UUID:
    return _id("agent", slug)


def team_kb_id() -> UUID:
    """UUID estable del KB ``team_shared`` (uno por equipo)."""
    return _id("kb", f"team:{TEAM_SLUG}")


def agent_kb_id(agent_slug: str) -> UUID:
    """UUID estable del KB ``private`` de un agente (uno por rol)."""
    return _id("kb", f"agent:{agent_slug}")


def kb_document_id(kb_slug: str, doc_slug: str) -> UUID:
    """UUID estable de un ``documents`` row dentro de un KB del seed."""
    return _id("kbdoc", f"{kb_slug}:{doc_slug}")


# =============================================================================
# Corpus del KB (task_demo_ws_01) — markdown versionado bajo scripts/webscorpo/kb/
# =============================================================================
# Raíz del corpus: 10 docs team-shared en ``team/`` + un ``agents/<role>/
# role-knowledge.md`` por agente. Generado del análisis en task_demo_ws_01.
KB_CORPUS_DIR: Path = _SCRIPTS_DIR / "webscorpo" / "kb"
_AGENT_DOC_NAME = "role-knowledge.md"
# MIME + plantilla de storage key sintética (el corpus vive en el repo, no en
# MinIO) — mismo patrón que ``catalog_ingestion`` para que la fila Document
# quede bien formada y un re-index futuro sepa de dónde viene.
_KB_MIME_TYPE = "text/markdown"
_KB_STORAGE_KEY_TEMPLATE = "webscorpo/kb/{rel}"


# =============================================================================
# Roster del equipo (análisis §7) — 10 agentes especializados en el stack
# =============================================================================
@dataclass(frozen=True)
class WebScorpoAgent:
    slug: str
    name: str
    role: str  # AgentRole value
    role_in_team: str
    description: str
    system_prompt: str
    # Slugs de tools built-in asignadas a este agente (junction agent_tools).
    tool_slugs: tuple[str, ...]
    is_team_leader: bool = False
    assignment_priority: int = 100
    review_capability: bool = False
    memory_scope: str = "team_shared"

    @property
    def id(self) -> UUID:
        return agent_id(self.slug)

    @property
    def kb_role(self) -> str:
        """Nombre del directorio del corpus por-rol (``agents/<kb_role>/``).

        El roster usa slugs ``webscorpo-<role>``; el corpus de task_demo_ws_01
        los guarda bajo ``agents/<role>/`` (sin el prefijo). Ej.:
        ``webscorpo-auth-security`` -> ``auth-security``.
        """
        return self.slug.removeprefix("webscorpo-")


# Conjuntos de tools reutilizables (slugs del catálogo built-in
# `api_server.seeds.builtin_tools`). shell_exec + file + git van a TODOS;
# run_* (tests/lint/typecheck/build) a backend/dba/qa/devops; http_get a
# auth-security/devops; semantic_search a todos (KB del equipo).
_FILE_TOOLS = ("read-file", "write-file", "apply-patch", "list-files", "search-code")
_GIT_TOOLS = ("git-status", "git-diff", "git-commit", "git-log")
_RUN_TOOLS = ("run-pytest", "run-lint", "run-typecheck", "run-build")
# Base que todo agente del equipo recibe: ejecutar comandos del stack
# (deny-by-default por allowed_commands), leer/editar el repo, git y la
# búsqueda semántica en el KB del equipo.
_BASE_TOOLS = ("shell-exec", *_FILE_TOOLS, *_GIT_TOOLS, "semantic-search")


WEBSCORPO_AGENTS: tuple[WebScorpoAgent, ...] = (
    WebScorpoAgent(
        slug="webscorpo-pm",
        name="WebScorpo PM",
        role="project_manager",
        role_in_team="Project / Delivery Manager",
        description=(
            "Owns the plan/Kanban, sequences module work, coordinates "
            "publish/version releases y el gate de deploy dual-region Azure; "
            "arbitra alcance vs. la iniciativa de cobertura (Phase 2)."
        ),
        system_prompt=(
            "Eres el Project/Delivery Manager del proyecto WebScorpo (CMS "
            "corporativo multi-tenant de Mediapro en CodeIgniter 4 + Doctrine + "
            "Twig + daycry/auth). Tu trabajo: descomponer objetivos en un Plan "
            "ejecutable con tareas, dependencias y estimaciones; secuenciar el "
            "trabajo por módulo (HMVC bajo app/Modules/); coordinar el ciclo "
            "publish/version de los WebProjects y el gate de deploy dual-region "
            "(EUS+WEU, solo en main). Arbitras alcance frente a la iniciativa de "
            "subir la cobertura desde el ~52.69% actual. NO escribes ni revisas "
            "código a fondo; delegas en backend/dba/frontend/qa/reviewer. Pides "
            "aprobación humana antes de mover un Plan a 'approved'."
        ),
        tool_slugs=(*_BASE_TOOLS, "send-notification"),
        is_team_leader=True,
        assignment_priority=10,
        memory_scope="project_shared",
    ),
    WebScorpoAgent(
        slug="webscorpo-architect",
        name="WebScorpo Architect",
        role="architect",
        role_in_team="Software Architect (CI4 + Doctrine)",
        description=(
            "Guarda la anatomía HMVC, los patrones Config+Items + BaseEntity + "
            "SLC, los helpers de routing config-driven (getRoutesDatatables/"
            "getRoutesBlocks) y las convenciones de columnas JSON/traducción."
        ),
        system_prompt=(
            "Eres el Software Architect de WebScorpo (CodeIgniter 4 + Doctrine "
            "ORM 3 vía daycry/doctrine). Guardas la arquitectura HMVC modular "
            "(app/Modules/{Zone}/{Module}/ con Config/Controllers/Database/"
            "Models/Traits/Views), el patrón Config+Items, BaseEntity "
            "(MappedSuperclass con uuid/timestamps/soft-delete/lifecycle) y la "
            "Second-Level Cache de Doctrine. Defiendes el routing config-driven "
            "de Config/WebsCorpo.php (getRoutesDatatables / getRoutesBlocks) en "
            "vez de rutas CRUD a mano, y las convenciones de columnas JSON de "
            'traducción ({"es":...,"en":...}). Documentas cada decisión como '
            "ADR (Contexto, Decisión, Alternativas, Consecuencias). Apruebas "
            "explícitamente cualquier desviación de estos patrones."
        ),
        tool_slugs=(*_BASE_TOOLS,),
        assignment_priority=20,
        review_capability=True,
        memory_scope="project_shared",
    ),
    WebScorpoAgent(
        slug="webscorpo-backend",
        name="WebScorpo Backend Dev",
        role="backend_dev",
        role_in_team="Backend Dev — CodeIgniter 4",
        description=(
            "CRUD de módulos día a día: controllers (web/Api/Config), "
            "Routes/Registrar/Validation, vistas Twig + partials, filtros. "
            "Domina las macros daycry/twig + DataTables + bloques."
        ),
        system_prompt=(
            "Eres Backend Dev especialista en CodeIgniter 4 en WebScorpo. "
            "Implementas el CRUD de módulos de contenido: controllers web, Api "
            "(REST /api/v1) y Config; Config/Routes.php + Registrar.php + "
            "{Module}Validation.php por módulo; vistas Twig 3 (daycry/twig) con "
            "los partials compartidos (input-forms/_field.twig, datatable.twig, "
            "blocks.twig). Conoces el sistema de bloques con render parcial AJAX, "
            "la macro DataTables (list/delete/visibility/order) y la cadena de "
            "filtros (auth:session, group:admin_corpo, webproject:admin, "
            "WebProjectFilter). Sigues las convenciones del repo y corres "
            "`composer ci` antes de dar por hecha una tarea. Si una decisión de "
            "diseño es no trivial, la consultas con el Arquitecto."
        ),
        tool_slugs=(*_BASE_TOOLS, *_RUN_TOOLS),
        assignment_priority=30,
        review_capability=True,
    ),
    WebScorpoAgent(
        slug="webscorpo-dba",
        name="WebScorpo DBA / Doctrine",
        role="backend_dev",
        role_in_team="Doctrine ORM / DBA",
        description=(
            "Dueño de entidades (attribute mapping, lifecycle, soft delete, "
            "UUID), repositorios + queries (Scienta JSON functions), "
            "migraciones per-module reversibles, seeds y regiones SLC."
        ),
        system_prompt=(
            "Eres el DBA / especialista Doctrine ORM de WebScorpo. Eres dueño de "
            "las ~38 entidades (mapeo por atributos #[ORM\\...], BaseEntity "
            "MappedSuperclass con #[ORM\\HasLifecycleCallbacks], soft-deletes vía "
            "deleted_at, UUID junto al PK numérico), los repositorios y queries "
            "custom (incluidas las Scienta Doctrine JSON Functions: JSON_EXTRACT/"
            "JSON_SET sobre columnas {es,en}), las migraciones per-module "
            "REVERSIBLES, los seeds, y la Second-Level Cache (regiones "
            "entity_read_heavy/entity_mixed/collection_* + invalidación en "
            "persist/update/remove). Generas proxies con `php spark "
            "DoctrineProxies`. El patrón es Config+Items: un singleton *_config "
            "+ N items con visible/position. Cualquier query lleva el contexto "
            "de WebProject."
        ),
        tool_slugs=(*_BASE_TOOLS, *_RUN_TOOLS),
        assignment_priority=40,
        review_capability=True,
    ),
    WebScorpoAgent(
        slug="webscorpo-frontend",
        name="WebScorpo Frontend Dev",
        role="frontend_dev",
        role_in_team="Frontend Dev",
        description=(
            "Assets Bootstrap 5.2.3 / jQuery / ES6 (jquery-form-validation.js, "
            "tinymce.js, language-tabs.js, bulk-actions.js), TinyMCE 7.3, "
            "versionado de assets y la macro de formulario _field.twig."
        ),
        system_prompt=(
            "Eres Frontend Dev de WebScorpo. Trabajas los assets en "
            "public/assets/js/core/ (jquery-form-validation.js con prevención de "
            "doble submit, tinymce.js, language-tabs.js, bulk-actions.js), la "
            "integración TinyMCE 7.3 (con el fix de z-index en modales), Select2, "
            "DataTables y Bootstrap 5.2.3. Conoces el versionado de assets "
            "(public/assets/versions.json) y el comportamiento de la macro de "
            "formulario _field.twig (modos standard/bare; selects traducidos con "
            "data-{locale}; controles por idioma con visibilidad por locale). "
            "Cuando una decisión afecta al backend (forma del payload, búsqueda, "
            "paginación), la negocias con Backend antes de implementar."
        ),
        tool_slugs=(*_BASE_TOOLS,),
        assignment_priority=50,
        review_capability=True,
    ),
    WebScorpoAgent(
        slug="webscorpo-auth-security",
        name="WebScorpo Auth/Security",
        role="security",
        role_in_team="Auth / Security (daycry/auth + Azure SSO)",
        description=(
            "Dueño de authenticators (session/JWT/access-token/guest), "
            "grupos/permisos (admin_corpo, webproject:admin), el bridge Azure "
            "AD SSO, password validators, rate-limiting y los hallazgos de "
            "seguridad (secretos hardcoded, AUTH_MODE=skip, JWT sin usar)."
        ),
        system_prompt=(
            "Eres el especialista de Auth/Security de WebScorpo. Eres dueño de "
            "daycry/auth (Config/Auth.php): authenticators session (default)/JWT/"
            "access-token/guest, validators de password (Composition, Dictionary, "
            "NothingPersonal), grupos/permisos (admin_corpo, webproject:admin), "
            "el flujo Azure AD SSO (LoginController::sso() + Mediapro\\GDI\\Library"
            "\\Azure, env gdi.*), el rate-limiting de la API REST y los módulos "
            "CSP/Cookies. Tu backlog de remediación incluye los hallazgos del "
            "análisis: secretos hardcoded en Config/WebsCorpo.php ($monitoringKey "
            "y $apiKeyForApiMonitoring), el path local $exifToolPath, el bypass "
            "AUTH_MODE=skip (jamás en prod) y el JWT configurado pero no cableado "
            "en rutas. Reportas con severidad y reproducción; no bloqueas un PR "
            "salvo por riesgo alto."
        ),
        tool_slugs=(*_BASE_TOOLS, "http-get"),
        assignment_priority=60,
        review_capability=True,
        memory_scope="project_shared",
    ),
    WebScorpoAgent(
        slug="webscorpo-i18n",
        name="WebScorpo i18n",
        role="specialist",
        role_in_team="i18n / Localization (EN/ES)",
        description=(
            "Ficheros de idioma EN/ES + daycry/codeigniter-language + el módulo "
            "Admin\\Language + columnas JSON de traducción + la UI language-tabs; "
            "garantiza que cada campo nuevo sea traducible en ambos locales."
        ),
        system_prompt=(
            "Eres el especialista de i18n/Localización de WebScorpo. Política: "
            "EN/ES únicamente (defaultLocale=en, supportedLocales=['en','es'], "
            "negotiateLocale=false). Trabajas los ficheros de idioma nativos de "
            "CI4 (Locales.php/Validation.php/Response.php por locale) + "
            "daycry/codeigniter-language + el módulo Admin\\Language "
            "(LanguagesRepository, LanguageValidation, language-tabs.twig) + el "
            "registro admin_languages y el array `languages` por config. Las "
            "rutas son locale-prefixed ({locale} -> /en/..., /es/...). Garantizas "
            "que CADA campo nuevo sea traducible (columnas JSON {es,en}) y que "
            "ambos locales estén cubiertos. No mezclas idiomas dentro de un valor."
        ),
        tool_slugs=(*_BASE_TOOLS,),
        assignment_priority=70,
    ),
    WebScorpoAgent(
        slug="webscorpo-qa",
        name="WebScorpo QA",
        role="qa",
        role_in_team="QA / Test Engineer",
        description=(
            "PHPUnit Unit/Integration + Selenium E2E (Login + WebProject), "
            "subir cobertura del 52.69%, fixtures de test DB + API-key, gates "
            "de mutación Infection y la disciplina strict-mode."
        ),
        system_prompt=(
            "Eres el QA / Test Engineer de WebScorpo. Las tres suites PHPUnit "
            "10.5 son Unit (tests/unit), Integration (tests/integration) y E2E "
            "(tests/E2E/Login + tests/E2E/WebProject, Selenium/Chrome con captura "
            "de pantalla). La config es strict (stopOnError/Failure/Warning, "
            "failOnRisky, failOnWarning) — tolerancia cero. Conoces phpunit.xml."
            "dist (encryption key, CSRF=session, FK=true, test DB group, "
            "X-API-KEY-TESTS) y tests/bootstrap.php (shutdown del driver Selenium). "
            "Usas los scripts composer: @test (Unit+Integration), @test-E2E, "
            "@test-coverage (HTML -> build/coverage/), @mutation (Infection 0.30 "
            "-> build/mutation/). Objetivo: subir la cobertura por encima del "
            "~52.69% actual (iniciativa Phase 2). Tu sesgo es romper, no validar."
        ),
        tool_slugs=(*_BASE_TOOLS, *_RUN_TOOLS),
        assignment_priority=80,
        review_capability=True,
    ),
    WebScorpoAgent(
        slug="webscorpo-reviewer",
        name="WebScorpo Reviewer",
        role="reviewer",
        role_in_team="Code Reviewer / Quality Gatekeeper",
        description=(
            "Hace cumplir @quality + @ci (CS-Fixer, PHPStan L2, Psalm, Rector, "
            "phpcpd, composer-unused) en cada PR; vigila baseline drift y los "
            "hallazgos de seguridad; dueño del gate merge-to-main."
        ),
        system_prompt=(
            "Eres el Code Reviewer / Quality Gatekeeper de WebScorpo. Haces "
            "cumplir el contrato @quality (cs-check + static-analysis phpstan+"
            "psalm + deduplicate phpcpd + unused-deps) y @ci (@quality + @test) "
            "en cada PR — lo que corre Azure por push. Revisas en orden: "
            "correctness, contexto multi-tenant de WebProject, seguridad y "
            "mantenibilidad. Vigilas el baseline drift de PHPStan L2 "
            "(phpstan-baseline.neon) y Psalm L4 (solo fallan issues NUEVOS), y "
            "que los hallazgos de seguridad abiertos no reaparezcan. Eres dueño "
            "del gate merge-to-main. Cuando el contexto trae un bloque "
            "<test-report>, lo citas como prueba dura. Terminas SIEMPRE con un "
            "<verdict>approve</verdict> o <verdict>reject</verdict>."
        ),
        tool_slugs=(*_BASE_TOOLS,),
        assignment_priority=90,
        review_capability=True,
        memory_scope="project_shared",
    ),
    WebScorpoAgent(
        slug="webscorpo-devops",
        name="WebScorpo DevOps",
        role="devops",
        role_in_team="DevOps / Release",
        description=(
            "Azure Pipelines, imagen Docker (PHP 8.4-FPM/Nginx/Supervisor), "
            "deploy dual-region EUS/WEU, endpoint de monitoring Zabbix, gestión "
            "de env/secretos y permisos de directorios escribibles."
        ),
        system_prompt=(
            "Eres el DevOps / Release Engineer de WebScorpo. Cuidas "
            "azure-pipelines.yml (stages Build -> Deploy EUS -> Deploy WEU; "
            "triggers release/development/main; deploy solo en main; template "
            "compartido refs/tags/v3.1.5), la imagen Docker (PHP 8.4-FPM Alpine "
            "+ Nginx + Supervisor, puerto 8080, dirs escribibles cache/logs/"
            "session/uploads en 755), el deploy dual-region en Azure App Service "
            "Linux (East US + West Europe), el health endpoint de Zabbix "
            "(/api/v1/monitoring/status), la gestión de env/secretos y la auth "
            "de los repos VCS de Composer en Azure DevOps (mediapro/gdi-library). "
            "Ante un fallo intermitente investigas la causa raíz; no añades "
            "retries como tapón. Documentas cada gotcha al resolverlo."
        ),
        tool_slugs=(*_BASE_TOOLS, *_RUN_TOOLS, "http-get", "http-post", "send-notification"),
        assignment_priority=100,
        review_capability=True,
        memory_scope="project_shared",
    ),
)


# =============================================================================
# Upsert SQL — idempotente por id (uuid5)
# =============================================================================
@dataclass
class KBIngestResult:
    """Resultado de ingestar un KB (KB + N documentos + chunks)."""

    slug: str  # "team" o "agent:<slug>"
    kb_id: UUID
    document_ids: dict[str, UUID] = field(default_factory=dict)  # doc_slug -> id
    chunks_persisted: int = 0
    embeddings_deferred: bool = False  # True si no había embedder alcanzable


@dataclass
class SeedResult:
    """Resumen de lo que el seed creó/actualizó (para logging + tests)."""

    tenant_id: UUID
    team_id: UUID
    project_id: UUID
    agent_ids: dict[str, UUID] = field(default_factory=dict)
    tool_assignments: dict[str, int] = field(default_factory=dict)
    # task_demo_ws_03: KBs (team-shared + por-agente) + ingesta del corpus.
    team_kb_id: UUID | None = None
    agent_kb_ids: dict[str, UUID] = field(default_factory=dict)  # agent_slug -> kb_id
    kb_documents: int = 0
    embeddings_deferred: bool = False


_UPSERT_ORG_SQL = """
    INSERT INTO organizations (id, name, slug, is_active)
    VALUES (:id, :name, :slug, true)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        slug = EXCLUDED.slug,
        is_active = true,
        updated_at = now(),
        deleted_at = NULL
"""

_UPSERT_TEAM_SQL = """
    INSERT INTO teams (id, tenant_id, name, description, shared_memory_namespace, is_builtin)
    VALUES (:id, :tenant_id, :name, :description, :ns, false)
    ON CONFLICT (id) DO UPDATE SET
        tenant_id = EXCLUDED.tenant_id,
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        shared_memory_namespace = EXCLUDED.shared_memory_namespace,
        updated_at = now(),
        deleted_at = NULL
"""

_UPSERT_AGENT_SQL = """
    INSERT INTO agents (
        id, tenant_id, name, description, agent_type, role,
        system_prompt, model_config, memory_scope, review_capability,
        max_concurrent_tasks, is_template, scope, project_id
    )
    VALUES (
        :id, :tenant_id, :name, :description, 'ai', :role,
        :system_prompt, '{}'::jsonb, :memory_scope, :review_capability,
        1, false, 'global_tenant_template', NULL
    )
    ON CONFLICT (id) DO UPDATE SET
        tenant_id = EXCLUDED.tenant_id,
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        role = EXCLUDED.role,
        system_prompt = EXCLUDED.system_prompt,
        memory_scope = EXCLUDED.memory_scope,
        review_capability = EXCLUDED.review_capability,
        scope = 'global_tenant_template',
        project_id = NULL,
        updated_at = now(),
        deleted_at = NULL
"""

_UPSERT_MEMBER_SQL = """
    INSERT INTO team_members (
        team_id, agent_id, role_in_team, is_team_leader, assignment_priority
    )
    VALUES (:team_id, :agent_id, :role_in_team, :is_team_leader, :priority)
    ON CONFLICT (team_id, agent_id) DO UPDATE SET
        role_in_team = EXCLUDED.role_in_team,
        is_team_leader = EXCLUDED.is_team_leader,
        assignment_priority = EXCLUDED.assignment_priority,
        updated_at = now()
"""

_DELETE_STALE_MEMBERS_SQL = """
    DELETE FROM team_members
     WHERE team_id = :team_id
       AND agent_id <> ALL(CAST(:keep_ids AS uuid[]))
"""

_UPSERT_PROJECT_SQL = """
    INSERT INTO projects (
        id, tenant_id, name, description, status, team_id,
        allowed_commands, default_runtime_template, is_template
    )
    VALUES (
        :id, :tenant_id, :name, :description, 'active', :team_id,
        CAST(:allowed_commands AS text[]), :runtime, false
    )
    ON CONFLICT (id) DO UPDATE SET
        tenant_id = EXCLUDED.tenant_id,
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        status = 'active',
        team_id = EXCLUDED.team_id,
        allowed_commands = EXCLUDED.allowed_commands,
        default_runtime_template = EXCLUDED.default_runtime_template,
        updated_at = now(),
        deleted_at = NULL
"""

_UPSERT_AGENT_TOOL_SQL = """
    INSERT INTO agent_tools (agent_id, tool_id)
    VALUES (:agent_id, :tool_id)
    ON CONFLICT (agent_id, tool_id) DO UPDATE SET updated_at = now()
"""

_DELETE_STALE_AGENT_TOOLS_SQL = """
    DELETE FROM agent_tools
     WHERE agent_id = :agent_id
       AND tool_id <> ALL(CAST(:keep_ids AS uuid[]))
"""

# --- KBs (task_demo_ws_03) — upsert idempotente por id (uuid5) ---------------
_UPSERT_KB_SQL = """
    INSERT INTO knowledge_bases
        (id, tenant_id, name, description, embedding_model_id, is_builtin)
    VALUES
        (:id, :tenant_id, :name, :description, 'nomic-embed-text-v1.5', false)
    ON CONFLICT (id) DO UPDATE SET
        tenant_id = EXCLUDED.tenant_id,
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        is_builtin = false,
        updated_at = now(),
        deleted_at = NULL
"""

_UPSERT_DOCUMENT_SQL = """
    INSERT INTO documents (
        id, tenant_id, kb_id, title, source_filename, source_mime_type,
        source_storage_key, source_size_bytes, status, page_count, indexed_at
    )
    VALUES (
        :id, :tenant_id, :kb_id, :title, :source_filename, :mime,
        :storage_key, :size_bytes, 'indexed', 0, now()
    )
    ON CONFLICT (id) DO UPDATE SET
        tenant_id = EXCLUDED.tenant_id,
        kb_id = EXCLUDED.kb_id,
        title = EXCLUDED.title,
        source_filename = EXCLUDED.source_filename,
        source_storage_key = EXCLUDED.source_storage_key,
        source_size_bytes = EXCLUDED.source_size_bytes,
        status = 'indexed',
        indexed_at = now(),
        error_message = NULL,
        updated_at = now(),
        deleted_at = NULL
"""

# El hash del corpus de un documento (en cualquiera de sus chunks). Sirve de
# token de idempotencia: si el .md no cambió, no re-troceamos/re-embebemos.
_EXISTING_CORPUS_HASH_SQL = """
    SELECT metadata->>'corpus_hash'
      FROM chunks
     WHERE document_id = :document_id
     LIMIT 1
"""

_DELETE_CHUNKS_SQL = "DELETE FROM chunks WHERE document_id = :document_id"

# Inserta un chunk. ``embedding`` puede ser NULL (degradación elegante sin
# embedder); BM25/keyword sigue sirviendo el chunk hasta el re-index. El ``id``
# es un default Python-side en el ORM, así que el INSERT crudo lo provee con un
# uuid5 estable por (document_id, ordinal) — re-insertar no cambia el id.
_INSERT_CHUNK_SQL = """
    INSERT INTO chunks (
        id, tenant_id, document_id, ordinal, content, embedding, bbox, metadata
    )
    VALUES (
        :id, :tenant_id, :document_id, :ordinal, :content,
        CAST(:embedding AS vector), NULL, CAST(:metadata AS jsonb)
    )
"""

# Borra documentos obsoletos de un KB (si el corpus encoge entre versiones).
_DELETE_STALE_DOCS_SQL = """
    DELETE FROM documents
     WHERE kb_id = :kb_id
       AND id <> ALL(CAST(:keep_ids AS uuid[]))
"""

# Grant KB->proyecto (junction kb_projects). tenant_id desnormalizado.
_UPSERT_KB_PROJECT_SQL = """
    INSERT INTO kb_projects (kb_id, project_id, tenant_id)
    VALUES (:kb_id, :project_id, :tenant_id)
    ON CONFLICT (kb_id, project_id) DO NOTHING
"""

# Grant KB->agente (junction agent_knowledge_bases). tenant_id desnormalizado.
_UPSERT_AGENT_KB_SQL = """
    INSERT INTO agent_knowledge_bases (agent_id, kb_id, tenant_id)
    VALUES (:agent_id, :kb_id, :tenant_id)
    ON CONFLICT (agent_id, kb_id) DO NOTHING
"""


async def seed_webscorpo(
    session: object,
    *,
    embedder: object | None = None,
    embedder_ok: bool | None = None,
) -> SeedResult:
    """Upserta el escenario WebScorpo completo. Idempotente.

    Caller debe pasar una ``AsyncSession`` ligada al engine admin
    (BYPASSRLS / migrations_user): el seed escribe en ``organizations`` y
    bajo el tenant de la plataforma (las tools built-in), lo que una
    sesión de tenant no puede hacer.

    ``embedder`` / ``embedder_ok`` se pasan tal cual a
    :func:`seed_webscorpo_kbs` (inyectable para los tests); por defecto
    (``None``) la fase de KBs construye el ``OllamaEmbedder`` real y
    comprueba su salud, degradando con elegancia si no está.

    Orden:
      1. ``ensure_platform_tenant`` + ``seed_builtin_tools`` — garantiza
         que TODAS las tools built-in (incl. ``shell_exec``) existan antes
         de asignarlas (FK en ``agent_tools.tool_id``).
      2. org Mediapro -> team WebScorpo -> 10 agentes -> membership.
      3. proyecto webscorpo (allowed_commands + php-phpunit + team).
      4. asignación de tools por agente (upsert + limpieza de obsoletas).
      5. KBs (team-shared + por-agente) + ingesta del corpus.
    """
    from api_server.seeds.builtin_tools import _tool_id, seed_builtin_tools
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy import text

    exec_ = session.execute  # type: ignore[attr-defined]

    # --- 1. Catálogo built-in: garantiza shell_exec + resto de tools -----
    await ensure_platform_tenant(session)  # type: ignore[arg-type]
    await seed_builtin_tools(session)  # type: ignore[arg-type]

    tid = tenant_id()
    tmid = team_id()
    pid = project_id()

    # --- 2. Org + team + agentes + membership ----------------------------
    await exec_(
        text(_UPSERT_ORG_SQL),
        {"id": str(tid), "name": TENANT_NAME, "slug": TENANT_SLUG},
    )
    await exec_(
        text(_UPSERT_TEAM_SQL),
        {
            "id": str(tmid),
            "tenant_id": str(tid),
            "name": TEAM_NAME,
            "description": (
                "Equipo especializado en WebScorpo (CodeIgniter 4 + Doctrine + "
                "Twig + daycry/auth): PM, Arquitecto, Backend CI4, DBA Doctrine, "
                "Frontend, Auth/Security, i18n, QA, Reviewer y DevOps."
            ),
            "ns": f"team:{TEAM_SLUG}",
        },
    )

    result = SeedResult(tenant_id=tid, team_id=tmid, project_id=pid)

    for agent in WEBSCORPO_AGENTS:
        await exec_(
            text(_UPSERT_AGENT_SQL),
            {
                "id": str(agent.id),
                "tenant_id": str(tid),
                "name": agent.name,
                "description": agent.description,
                "role": agent.role,
                "system_prompt": agent.system_prompt,
                "memory_scope": agent.memory_scope,
                "review_capability": agent.review_capability,
            },
        )
        result.agent_ids[agent.slug] = agent.id

    for agent in WEBSCORPO_AGENTS:
        await exec_(
            text(_UPSERT_MEMBER_SQL),
            {
                "team_id": str(tmid),
                "agent_id": str(agent.id),
                "role_in_team": agent.role_in_team,
                "is_team_leader": agent.is_team_leader,
                "priority": agent.assignment_priority,
            },
        )
    # Limpia miembros obsoletos (si el roster encoge entre versiones).
    await exec_(
        text(_DELETE_STALE_MEMBERS_SQL),
        {"team_id": str(tmid), "keep_ids": [str(a.id) for a in WEBSCORPO_AGENTS]},
    )

    # --- 3. Proyecto webscorpo (Plan 06.16: allowed_commands + runtime) --
    await exec_(
        text(_UPSERT_PROJECT_SQL),
        {
            "id": str(pid),
            "tenant_id": str(tid),
            "name": PROJECT_NAME,
            "description": (
                "WebScorpo — CMS corporativo multi-tenant de Mediapro "
                "(CodeIgniter 4 + Doctrine + Twig + daycry/auth). Stack PHP 8.2+; "
                "tests PHPUnit/Selenium; CI/CD Azure dual-region. Proyecto real "
                "en C:/laragon/www/webscorpo (solo-lectura)."
            ),
            "team_id": str(tmid),
            "allowed_commands": list(PROJECT_ALLOWED_COMMANDS),
            "runtime": PROJECT_RUNTIME_TEMPLATE,
        },
    )

    # --- 4. Asignación de tools por agente (junction agent_tools) --------
    for agent in WEBSCORPO_AGENTS:
        keep_ids = [str(_tool_id(slug)) for slug in agent.tool_slugs]
        for slug in agent.tool_slugs:
            await exec_(
                text(_UPSERT_AGENT_TOOL_SQL),
                {"agent_id": str(agent.id), "tool_id": str(_tool_id(slug))},
            )
        await exec_(
            text(_DELETE_STALE_AGENT_TOOLS_SQL),
            {"agent_id": str(agent.id), "keep_ids": keep_ids},
        )
        result.tool_assignments[agent.slug] = len(keep_ids)

    # --- 5. KBs (team-shared + por-agente) + ingesta del corpus ----------
    await seed_webscorpo_kbs(session, result, embedder=embedder, embedder_ok=embedder_ok)

    return result


# =============================================================================
# KBs + ingesta del corpus (task_demo_ws_03)
# =============================================================================
# Un KB **team_shared** con los 10 docs ``team/*.md``, concedido al proyecto
# (kb_projects) y a TODOS los agentes del equipo (agent_knowledge_bases); y un
# KB **private** por agente con su ``agents/<role>/role-knowledge.md``,
# concedido sólo a ese agente. La ingesta reutiliza el pipeline de Plan 04
# (chunker markdown de ``catalog_ingestion`` + ``Embedder`` Protocol) con
# **degradación elegante**: si no hay embedder (Ollama) alcanzable, los
# documentos + chunks se persisten con ``embedding = NULL`` y se difiere el
# embedding al re-index, sin reventar el seed.


def _frontmatter_title(corpus: str, fallback: str) -> str:
    """Extrae ``title:`` del frontmatter YAML del corpus (o ``fallback``)."""
    lines = corpus.splitlines()
    if not lines or lines[0].strip() != "---":
        return fallback
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("title:"):
            value = line.partition(":")[2].strip().strip('"').strip("'")
            if value:
                return value
    return fallback


def _corpus_hash(corpus: str) -> str:
    """SHA-256 estable del corpus — token de idempotencia por documento."""
    import hashlib

    return hashlib.sha256(corpus.encode("utf-8")).hexdigest()


async def _ingest_document(
    session: object,
    *,
    embedder: object,
    embedder_ok: bool,
    tenant: UUID,
    kb: UUID,
    kb_slug: str,
    doc_slug: str,
    md_path: Path,
) -> tuple[UUID, int, bool]:
    """Upserta UN documento + sus chunks. Idempotente; degrada sin embedder.

    Devuelve ``(document_id, n_chunks, embeddings_deferred)``. Reutiliza el
    chunker markdown de ``catalog_ingestion`` y embebe vía el ``Embedder``
    inyectado; ante ``EmbeddingError`` (o si el embedder no estaba alcanzable)
    persiste ``embedding = NULL`` y marca el documento como diferido.
    """
    from api_server.ingestion.embeddings import EmbeddingError
    from api_server.seeds.catalog_ingestion import chunk_markdown
    from sqlalchemy import text

    exec_ = session.execute  # type: ignore[attr-defined]
    corpus = md_path.read_text(encoding="utf-8")
    document_id = kb_document_id(kb_slug, doc_slug)
    corpus_hash = _corpus_hash(corpus)
    rel = md_path.relative_to(KB_CORPUS_DIR).as_posix()

    # Fast-path idempotente: si los chunks ya llevan el hash actual, no
    # re-troceamos/re-embebemos (mismo recurso que catalog_ingestion).
    existing_hash = (
        await exec_(text(_EXISTING_CORPUS_HASH_SQL), {"document_id": str(document_id)})
    ).scalar_one_or_none()

    # El documento (fila ``documents``) siempre se upserta (metadata barata).
    await exec_(
        text(_UPSERT_DOCUMENT_SQL),
        {
            "id": str(document_id),
            "tenant_id": str(tenant),
            "kb_id": str(kb),
            "title": _frontmatter_title(corpus, fallback=md_path.stem),
            "source_filename": md_path.name,
            "mime": _KB_MIME_TYPE,
            "storage_key": _KB_STORAGE_KEY_TEMPLATE.format(rel=rel),
            "size_bytes": len(corpus.encode("utf-8")),
        },
    )

    if existing_hash == corpus_hash:
        # Corpus sin cambios -> conservamos los chunks (y sus embeddings).
        count = int(
            (
                await exec_(
                    text("SELECT count(*) FROM chunks WHERE document_id = :id"),
                    {"id": str(document_id)},
                )
            ).scalar_one()
        )
        return document_id, count, False

    chunk_texts = chunk_markdown(corpus)

    # Embebe en un batch; degrada a NULL si el embedder falla / no está.
    embeddings: list[list[float] | None]
    deferred = False
    if embedder_ok and chunk_texts:
        try:
            vectors = await embedder.embed(chunk_texts)  # type: ignore[attr-defined]
            embeddings = list(vectors)
        except EmbeddingError as exc:
            print(f"    [embedder] no alcanzable ({exc}); embeddings diferidos.")
            embeddings = [None] * len(chunk_texts)
            deferred = True
    else:
        embeddings = [None] * len(chunk_texts)
        deferred = not embedder_ok and bool(chunk_texts)

    # Reemplazo atómico de chunks: borra los viejos, inserta el set nuevo.
    await exec_(text(_DELETE_CHUNKS_SQL), {"document_id": str(document_id)})
    for ordinal, (content, embedding) in enumerate(zip(chunk_texts, embeddings, strict=True)):
        await exec_(
            text(_INSERT_CHUNK_SQL),
            {
                "id": str(_id("chunk", f"{document_id}:{ordinal}")),
                "tenant_id": str(tenant),
                "document_id": str(document_id),
                "ordinal": ordinal,
                "content": content,
                # pgvector acepta el literal "[...]"; NULL difiere el embedding.
                "embedding": (
                    "[" + ",".join(repr(float(x)) for x in embedding) + "]"
                    if embedding is not None
                    else None
                ),
                "metadata": _json_dumps(
                    {"corpus_hash": corpus_hash, "source": "webscorpo", "slug": doc_slug}
                ),
            },
        )
    return document_id, len(chunk_texts), deferred


def _json_dumps(obj: dict[str, object]) -> str:
    import json

    return json.dumps(obj)


async def _make_embedder() -> tuple[object | None, bool]:
    """Construye el ``OllamaEmbedder`` y comprueba si Ollama está alcanzable.

    Degradación elegante: si la construcción falla o un ping rápido a Ollama
    no responde, devolvemos ``(embedder_or_None, False)`` y el seed persiste
    documentos sin embeddings (diferidos al re-index). Nunca revienta.
    """
    from api_server.ingestion.embeddings import EmbeddingError, OllamaEmbedder

    try:
        embedder = OllamaEmbedder()
    except Exception as exc:  # — degradar ante cualquier fallo de wiring
        print(f"    [embedder] no se pudo construir ({exc}); embeddings diferidos.")
        return None, False

    # Ping barato: embebe un texto trivial. Si falla, degradamos.
    try:
        await embedder.embed(["webscorpo embedder health-check"])
    except EmbeddingError as exc:
        print(f"    [embedder] Ollama no alcanzable ({exc}); embeddings diferidos.")
        return embedder, False
    return embedder, True


async def seed_webscorpo_kbs(
    session: object,
    result: SeedResult,
    *,
    embedder: object | None = None,
    embedder_ok: bool | None = None,
) -> SeedResult:
    """Crea/upserta el KB team-shared + los KBs por-agente e ingesta el corpus.

    Idempotente (upsert por uuid5 estable). Degradación elegante del embedder.
    Asume que ``seed_webscorpo`` ya creó tenant/equipo/agentes/proyecto (los
    ids viven en ``result``). El caller posee la transacción.

    El embedder es **inyectable** (mismo patrón que ``catalog_ingestion``): por
    defecto construye el ``OllamaEmbedder`` real y comprueba su salud; los
    tests pasan un fake determinista (o ``embedder_ok=False`` para ejercitar la
    degradación). Cuando el caller inyecta el embedder, NO lo cerramos (lo
    posee él).
    """
    from sqlalchemy import text

    exec_ = session.execute  # type: ignore[attr-defined]
    tid = result.tenant_id
    team_dir = KB_CORPUS_DIR / "team"

    own_embedder = embedder is None and embedder_ok is None
    if own_embedder:
        embedder, embedder_ok = await _make_embedder()
    elif embedder_ok is None:
        embedder_ok = embedder is not None
    try:
        # --- KB team_shared con los 10 docs team/*.md -------------------
        tkb = team_kb_id()
        await exec_(
            text(_UPSERT_KB_SQL),
            {
                "id": str(tkb),
                "tenant_id": str(tid),
                "name": "WebScorpo — Conocimiento del equipo",
                "description": (
                    "KB compartido del equipo WebScorpo: overview + glosario, mapa "
                    "de arquitectura HMVC, routing/filtros, data-model Doctrine/"
                    "BaseEntity/SLC, estándares + toolchain (composer @ci/@quality/"
                    "@fix/@test*/@mutation), estrategia de tests, runbook CI/CD "
                    "Azure dual-region, política i18n EN/ES, baseline de seguridad y "
                    "catálogo de dependencias daycry/*. Derivado del análisis."
                ),
            },
        )
        result.team_kb_id = tkb

        team_doc_ids: list[str] = []
        team_deferred = False
        if team_dir.is_dir():
            for md_path in sorted(team_dir.glob("*.md")):
                doc_id, n_chunks, deferred = await _ingest_document(
                    session,
                    embedder=embedder,
                    embedder_ok=embedder_ok,
                    tenant=tid,
                    kb=tkb,
                    kb_slug="team",
                    doc_slug=md_path.stem,
                    md_path=md_path,
                )
                team_doc_ids.append(str(doc_id))
                result.kb_documents += 1
                team_deferred = team_deferred or deferred
        # Limpia documentos obsoletos del KB del equipo.
        await exec_(
            text(_DELETE_STALE_DOCS_SQL),
            {"kb_id": str(tkb), "keep_ids": team_doc_ids or [str(tkb)]},
        )
        result.embeddings_deferred = result.embeddings_deferred or team_deferred

        # Grant del KB del equipo: al proyecto + a TODOS los agentes.
        await exec_(
            text(_UPSERT_KB_PROJECT_SQL),
            {"kb_id": str(tkb), "project_id": str(result.project_id), "tenant_id": str(tid)},
        )
        for agent in WEBSCORPO_AGENTS:
            await exec_(
                text(_UPSERT_AGENT_KB_SQL),
                {"agent_id": str(agent.id), "kb_id": str(tkb), "tenant_id": str(tid)},
            )

        # --- KB private por agente con su doc de rol --------------------
        for agent in WEBSCORPO_AGENTS:
            akb = agent_kb_id(agent.slug)
            await exec_(
                text(_UPSERT_KB_SQL),
                {
                    "id": str(akb),
                    "tenant_id": str(tid),
                    "name": f"WebScorpo — {agent.role_in_team}",
                    "description": (
                        f"KB privado del agente {agent.name} ({agent.role_in_team}): "
                        "conocimiento específico de su rol en WebScorpo, derivado del "
                        "análisis. Concedido sólo a este agente."
                    ),
                },
            )
            result.agent_kb_ids[agent.slug] = akb

            role_md = KB_CORPUS_DIR / "agents" / agent.kb_role / _AGENT_DOC_NAME
            agent_doc_ids: list[str] = []
            if role_md.is_file():
                doc_id, _n, deferred = await _ingest_document(
                    session,
                    embedder=embedder,
                    embedder_ok=embedder_ok,
                    tenant=tid,
                    kb=akb,
                    kb_slug=f"agent:{agent.slug}",
                    doc_slug=agent.kb_role,
                    md_path=role_md,
                )
                agent_doc_ids.append(str(doc_id))
                result.kb_documents += 1
                result.embeddings_deferred = result.embeddings_deferred or deferred
            await exec_(
                text(_DELETE_STALE_DOCS_SQL),
                {"kb_id": str(akb), "keep_ids": agent_doc_ids or [str(akb)]},
            )
            # Grant del KB privado: sólo a su agente.
            await exec_(
                text(_UPSERT_AGENT_KB_SQL),
                {"agent_id": str(agent.id), "kb_id": str(akb), "tenant_id": str(tid)},
            )
    finally:
        if own_embedder and embedder is not None:
            with contextlib.suppress(Exception):
                await embedder.aclose()  # type: ignore[attr-defined]

    return result


# =============================================================================
# CLI
# =============================================================================
def _default_db_url() -> str:
    """URL del engine admin (migrations_user / BYPASSRLS) del stack dev.

    Reutiliza ``DEMO_DATABASE_URL`` de ``_demo_common`` si está definida; si
    no, cae al default del stack dev (PG 15432). El seed necesita BYPASSRLS
    para escribir en ``organizations`` y bajo el tenant de la plataforma.
    """
    return os.environ.get(
        "WEBSCORPO_DATABASE_URL",
        os.environ.get(
            "DEMO_DATABASE_URL",
            "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
            "@localhost:15432/agentic_platform",
        ),
    )


async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    print("=" * 72)
    print("  seed WebScorpo — tenant Mediapro + equipo + 10 agentes + proyecto + tools")
    print("=" * 72)

    engine = create_async_engine(_default_db_url())
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            result = await seed_webscorpo(session)
        print(f"  tenant:  {TENANT_NAME} ({result.tenant_id})")
        print(f"  team:    {TEAM_NAME} ({result.team_id})")
        print(f"  project: {PROJECT_NAME} ({result.project_id})")
        print(f"           allowed_commands = {list(PROJECT_ALLOWED_COMMANDS)}")
        print(f"           default_runtime_template = {PROJECT_RUNTIME_TEMPLATE}")
        print(f"  agentes: {len(result.agent_ids)}")
        for slug, n_tools in result.tool_assignments.items():
            print(f"    - {slug}: {n_tools} tools")
        print(f"  KB equipo: {result.team_kb_id}")
        print(f"  KBs por-agente: {len(result.agent_kb_ids)}")
        print(f"  documentos KB ingestados: {result.kb_documents}")
        if result.embeddings_deferred:
            print(
                "  AVISO: sin embedder (Ollama) alcanzable -> embeddings diferidos."
                " Re-indexa cuando haya proveedor configurado."
            )
        print()
        print("  OK — idempotente: re-ejecutar no duplica nada.")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
