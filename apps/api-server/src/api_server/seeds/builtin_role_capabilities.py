"""Default capability map per agent ROLE for built-in teams (Ola B / ADR 0055).

Los equipos built-in deben salir "completos": cada agente con tools + skills
sensatas por su rol. Este módulo es la fuente DRY del conjunto de SKILLS por rol;
las tools por equipo siguen en el seed del equipo, y un agente puede OVERRIDEar
con sus propios `skill_slugs` (p.ej. el equipo PHP CodeIgniter 4 pone php-phpunit
/ codeigniter4-hmvc / doctrine-orm en sus backend_dev).

Aquí solo skills AGNÓSTICAS de stack (aplican a cualquier lenguaje), porque el
mapa lo comparten TODOS los equipos built-in. Los slugs salen del catálogo
`builtin_skills.py` (incluye las añadidas en la Ola B0.1).
"""

from __future__ import annotations

# rol (AgentRole value) -> slugs de skill del catálogo `builtin_skills.py`.
ROLE_DEFAULT_SKILLS: dict[str, tuple[str, ...]] = {
    "project_manager": ("cost-benefit-analysis", "structured-writing"),
    "architect": ("adr-authoring", "technical-comparison", "mermaid-diagrams"),
    "backend_dev": (
        "database-migrations",
        "api-versioning",
        "sql-optimization",
        "secure-coding-owasp",
    ),
    "frontend_dev": ("responsive-design", "accessibility-aria", "web-performance"),
    "security": ("secure-coding-owasp", "dependency-audit-sca", "secrets-vault"),
    "specialist": ("technical-comparison", "evidence-collection", "web-research"),
    "qa": (
        "test-pyramid-design",
        "regression-test-strategy",
        "edge-case-identification",
        "contract-testing",
    ),
    "reviewer": (
        "regression-test-strategy",
        "edge-case-identification",
        "secure-coding-owasp",
    ),
    "devops": (
        "docker-compose-orchestration",
        "github-actions-ci",
        "observability-otel",
        "backup-recovery",
    ),
}


def default_skill_slugs(role: str) -> tuple[str, ...]:
    """Skills por defecto de un rol (vacío si el rol no está mapeado)."""
    return ROLE_DEFAULT_SKILLS.get(role, ())


__all__ = ["ROLE_DEFAULT_SKILLS", "default_skill_slugs"]
