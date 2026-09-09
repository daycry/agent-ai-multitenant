"""Ninguna ruta del panel desaparece durante la reestructuración de la UI.

Plan [`ui-reestructuracion-2026-09-09`](../../docs/roadmap/ui-reestructuracion-2026-09-09.md),
`task_ui_04`. El plan reorganiza **dónde se enseñan** las pantallas del panel —tres
áreas, el proyecto como hub, barras laterales distintas— y dice por escrito que
«las rutas actuales se conservan: cambia dónde se enseñan y qué barra las envuelve,
no las URL». Esta guarda es lo que convierte esa frase en algo comprobable.

## Por qué la foto se toma AHORA y no al final

Porque una guarda de conservación capturada después de mover las pantallas no
conserva nada: fija el resultado, incluido lo que se perdió por el camino. El
plan lista esta casilla al final de la ola 1, pero su trabajo útil —la línea
base— tiene que existir antes de la primera casilla que toca `admin-shell.tsx`.
Por eso se entrega primero.

## Qué modo de fallo cubre

Un enlace que muere no rompe ningún test del panel: `next build` compila igual,
vitest no visita rutas y el usuario se encuentra un 404 en un sitio al que
llegaba desde su marcador, su historial o un correo de notificación. Y el
síntoma no dice si la pantalla se movió, se renombró o se borró.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO_ROOT / "apps" / "admin-panel" / "app"

#: La foto de las rutas del panel el **2026-09-09**, tomada ANTES de la primera
#: casilla que mueve pantallas (`task_ui_01`). Son 81 `page.tsx`: las 70 de
#: `/admin` más el login, la aceptación de invitación, el callback de OAuth, las
#: cinco de `/developers` y las dos pantallas sin tenant. Se generó con
#: `_discover_routes` sobre el árbol limpio, no a mano.
#:
#: **Esta lista no se edita para arreglar un rojo.** Un rojo aquí dice que una URL
#: que alguien tiene en un marcador, en su historial o en un correo de
#: notificación dejó de existir. El arreglo es devolver la ruta —aunque sea
#: redirigiendo—, no borrar su línea. Si de verdad hay que retirar una pantalla,
#: se retira con su línea en el changelog del plan y se quita de aquí en el MISMO
#: commit.
ROUTES_AT_THE_START_OF_THE_PLAN: tuple[str, ...] = (
    "/",
    "/accept-invite",
    "/admin/agents",
    "/admin/agents/[id]",
    "/admin/approval-policy",
    "/admin/approvals",
    "/admin/assistant",
    "/admin/assistant/settings",
    "/admin/backup",
    "/admin/backup/destinations",
    "/admin/backup/restore",
    "/admin/board",
    "/admin/cortex",
    "/admin/cortex/identity",
    "/admin/cortex/mind",
    "/admin/dashboard",
    "/admin/docs",
    "/admin/documents",
    "/admin/documents/[id]/citations",
    "/admin/documents/[id]/ingestion",
    "/admin/eval-quality",
    "/admin/executions/[id]",
    "/admin/guardrails",
    "/admin/human-agents",
    "/admin/human-queue",
    "/admin/inbox",
    "/admin/invitations",
    "/admin/knowledge-bases",
    "/admin/knowledge-bases/categories",
    "/admin/leaderboard",
    "/admin/llm-providers",
    "/admin/marketplace",
    "/admin/marketplace/installations/[id]",
    "/admin/marketplace/installations/[id]/permissions",
    "/admin/marketplace/private",
    "/admin/marketplace/review",
    "/admin/memories",
    "/admin/model-prices",
    "/admin/notifications",
    "/admin/notifications/inbox",
    "/admin/office",
    "/admin/ollama",
    "/admin/plans/[id]/escalated",
    "/admin/projects",
    "/admin/projects/[id]",
    "/admin/projects/[id]/agent-tools-diagnostic",
    "/admin/projects/[id]/chat",
    "/admin/projects/[id]/commands",
    "/admin/projects/[id]/dep-cache",
    "/admin/projects/[id]/incoming-webhooks",
    "/admin/projects/[id]/knowledge-bases",
    "/admin/projects/[id]/mcp-servers",
    "/admin/projects/[id]/memories",
    "/admin/projects/[id]/plans",
    "/admin/projects/[id]/plans/[planId]",
    "/admin/projects/[id]/tasks",
    "/admin/projects/new",
    "/admin/review/[id]",
    "/admin/review/active",
    "/admin/runs",
    "/admin/settings",
    "/admin/settings/hourly-rate",
    "/admin/settings/memories",
    "/admin/settings/platform-defaults",
    "/admin/settings/security",
    "/admin/settings/sso",
    "/admin/settings/sso/saml",
    "/admin/teams",
    "/admin/teams/[team_id]",
    "/admin/tenant-stats",
    "/admin/tools",
    "/admin/users",
    "/auth/callback",
    "/developers",
    "/developers/api-reference",
    "/developers/sdks",
    "/developers/tutorials",
    "/developers/webhooks",
    "/login",
    "/no-access",
    "/select-tenant",
)


def _routes_that_disappeared(baseline: tuple[str, ...], discovered: set[str]) -> list[str]:
    """Las rutas de la línea base que ya no están en el árbol, ordenadas."""
    return sorted(set(baseline) - discovered)


def _discover_routes(app_dir: Path) -> set[str]:
    """Las URL que sirve el App Router, sacadas de los `page.tsx` del árbol.

    Dos reglas de Next.js que hay que respetar para no comparar peras con
    manzanas: un **grupo de rutas** `(nombre)` organiza ficheros sin aparecer en
    la URL, y los segmentos dinámicos `[id]` sí aparecen (se conservan con sus
    corchetes, que es como los escribe el plan). Todo lo demás es el camino
    relativo a `app/`, con `/` para la raíz.
    """
    routes: set[str] = set()
    for page in app_dir.rglob("page.tsx"):
        segments = [
            part
            for part in page.relative_to(app_dir).parent.parts
            if not (part.startswith("(") and part.endswith(")"))
        ]
        routes.add("/" + "/".join(segments) if segments else "/")
    return routes


def test_the_guard_notices_a_route_that_disappeared() -> None:
    """El autotest de la guarda: sin él, la comparación puede pasar vacía.

    `docs/03-guides/verificar-antes-de-implementar.md` §4: una guarda estática que
    deja de encontrar sujetos pasa en verde y envejece sin avisar. Aquí se
    comprueba con datos de juguete que la comparación **sí** ve la pérdida, para
    que el verde de abajo signifique algo.
    """
    baseline = ("/admin/dashboard", "/admin/board", "/admin/inbox")
    discovered = {"/admin/dashboard", "/admin/inbox"}

    assert _routes_that_disappeared(baseline, discovered) == ["/admin/board"]
    assert _routes_that_disappeared(baseline, set(baseline)) == []


def test_the_discovery_finds_the_panel_screens() -> None:
    """Sin universo, la comparación de abajo compara la nada con la nada.

    El árbol traía **70** `page.tsx` el 2026-09-09. Se exige un suelo holgado
    (60) en vez del número exacto: el plan añade pantallas a propósito, y una
    guarda que se rompa al añadir la primera se desactivaría el mismo día.
    """
    discovered = _discover_routes(_APP_DIR)

    assert len(discovered) >= 60, (
        f"el descubrimiento de pantallas del panel encontró {len(discovered)}; "
        "si el árbol se movió de sitio, muévelo aquí en el mismo commit"
    )
    for expected in ("/admin/dashboard", "/admin/projects/[id]", "/login"):
        assert expected in discovered, f"la ruta {expected} no la ve el descubrimiento"


def test_no_admin_route_disappeared() -> None:
    """La promesa del plan, comprobada: las URL de antes siguen respondiendo.

    Las rutas **nuevas** no se declaran aquí a propósito. El plan añade pantallas
    —el tablero dentro del proyecto, el portfolio— y una guarda que además
    obligara a declarar cada añadido sería papeleo, y el papeleo se desactiva. Lo
    que ésta protege es una sola dirección, que es la que duele: que no se pierda
    ninguna.
    """
    desaparecidas = _routes_that_disappeared(
        ROUTES_AT_THE_START_OF_THE_PLAN, _discover_routes(_APP_DIR)
    )

    assert not desaparecidas, (
        "estas rutas del panel existían al arrancar el plan "
        "`ui-reestructuracion-2026-09-09` y ya no las sirve ningún `page.tsx`:\n"
        + "\n".join(f"  {route}" for route in desaparecidas)
    )
