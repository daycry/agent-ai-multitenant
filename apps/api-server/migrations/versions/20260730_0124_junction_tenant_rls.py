"""tenant_id + RLS en las 4 tablas de unión (plan prod-14, hallazgo tenancy-1).

La migración 0002 dejó `agent_skills`, `agent_tools`, `team_members` y
`task_dependencies` fuera de `_TENANT_SCOPED_TABLES` con este razonamiento:
«las junctions dependen de la visibilidad del padre vía ON DELETE CASCADE».
Eso es cierto para el BORRADO en cascada, y falso para todo lo demás: sin
columna `tenant_id` no hay política RLS posible, así que a nivel de base de
datos cualquier sesión podía

  * LEER las asignaciones de otro tenant —incluido `agent_tools.config_override`,
    que transporta configuración por agente—, y
  * INSERTAR una fila apuntando a un padre ajeno (las comprobaciones de clave
    ajena se ejecutan como el propietario de la tabla e IGNORAN la RLS, así que
    la FK no protege nada aquí).

Que no hubiera fuga explotable dependía por completo de que cada router hiciera
su chequeo de visibilidad antes de escribir. Esta migración convierte esa
disciplina en un invariante de la base de datos (Principio Rector nº 1).

## Qué hace

1. **Pre-check ruidoso**: aborta la migración si encuentra filas de unión
   genuinamente incoherentes (padre e hijo de tenants distintos). Sin él, el
   backfill «elegiría» un tenant_id y consolidaría el desastre en silencio.
2. `tenant_id UUID` + backfill desde el padre PROPIETARIO + `NOT NULL` + índice.
3. Un **trigger BEFORE INSERT OR UPDATE por tabla que DERIVA `tenant_id` del
   padre** y rechaza cualquier valor que lo contradiga.
4. RLS `ENABLE` + `FORCE` + policy `{tabla}_tenant_isolation` con `USING` y
   `WITH CHECK`, idéntica en forma a las de la 0002/0004.
5. Policies `{tabla}_builtin_read` (SELECT-only) para las tres junctions que
   cuelgan de catálogo de plataforma.

## Por qué un trigger y no «que cada llamante pase tenant_id»

El plan original (task_prod14_02) pedía revisar todos los caminos de escritura
para que pasaran `tenant_id` explícito. El trigger es estrictamente mejor y
además hace innecesaria esa tarea:

  * **Cubre el caso que la policy NO puede cubrir.** Bajo `app_user` (NOBYPASSRLS)
    la omisión de `tenant_id` revienta sola contra el `WITH CHECK`. Pero los
    servicios (workers, orchestrator, dispatcher, engine admin de la api-server)
    conectan con un rol BYPASSRLS: ahí ninguna policy mira. El trigger sí, porque
    los triggers se ejecutan para todos los roles, BYPASSRLS incluido. Un servicio
    comprometido que intente escribir una fila con el `tenant_id` de otro tenant
    es rechazado con `check_violation`.
  * **No depende de la disciplina de la próxima escritura.** Es exactamente el
    objetivo declarado del plan: que el aislamiento deje de depender de que cada
    query futura se acuerde.
  * **Cero coste de acoplamiento**: los ~12 puntos de escritura (routers de
    agentes/equipos/tareas, el fork de agentes, la adopción de equipos, cinco
    seeds y la instalación del marketplace) siguen funcionando sin cambios.

El trigger NO es `SECURITY DEFINER` a propósito: se ejecuta con los privilegios
del llamante, así que su `SELECT` sobre el padre respeta la RLS del padre. Bajo
`app_user`, un padre de otro tenant simplemente no existe → el trigger levanta
`insufficient_privilege` y la fila no entra. Fail-closed sin escalada de
privilegios y sin superficie de `SECURITY DEFINER` que auditar.

## Por qué hay policies `builtin_read`

Porque el catálogo de plataforma vive bajo `PLATFORM_TENANT_ID` y hay dos flujos
en producción que LEEN sus filas de unión desde la sesión de OTRO tenant:

  * `_clone_agent_capabilities` (routers/agents.py) copia `agent_tools` y
    `agent_skills` del agente origen al forkearlo;
  * `_fork_team_deep` (routers/teams.py) lee los `team_members` del equipo
    built-in para recrearlos al adoptarlo.

Con una policy estricta esas lecturas devolverían 0 filas y el fallo sería
SILENCIOSO: forkear un agente built-in daría un agente sin herramientas, y
adoptar un equipo built-in daría un equipo VACÍO. Las tres policies replican el
patrón ya existente de `agents_global_builtin_read` (0004) y `{skills,tools}_
builtin_read` (0005): SELECT-only, así que la escritura sigue gobernada por el
`WITH CHECK` de la policy de aislamiento.

`task_dependencies` no lleva `builtin_read`: no existe catálogo de tareas de
plataforma. Su trigger además exige que AMBAS tareas sean del mismo tenant —
una dependencia cross-tenant es un DAG imposible y ni un servicio BYPASSRLS
debería poder crearla.

Revision ID: 0124_junction_tenant_rls
Revises: 0123_cortex_pursuit_approved
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0124_junction_tenant_rls"
down_revision: str | Sequence[str] | None = "0123_cortex_pursuit_approved"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# (tabla, tabla del padre propietario, columna FK al padre)
#
# El padre PROPIETARIO es el que define el tenant de la fila de unión:
#   * agent_skills / agent_tools → el AGENTE (una skill/tool built-in de
#     plataforma asignada a un agente del tenant A es una fila del tenant A);
#   * team_members → el EQUIPO (un agente built-in puede ser miembro del
#     equipo del tenant A: la membresía es de A);
#   * task_dependencies → la TAREA dependiente.
# ---------------------------------------------------------------------------
_JUNCTIONS: tuple[tuple[str, str, str], ...] = (
    ("agent_skills", "agents", "agent_id"),
    ("agent_tools", "agents", "agent_id"),
    ("team_members", "teams", "team_id"),
    ("task_dependencies", "tasks", "task_id"),
)

# Filas de unión legítimamente «cross-tenant» porque el hijo es catálogo de
# plataforma. El pre-check las exime; todo lo demás aborta la migración.
_CONSISTENCY_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "agent_skills",
        """
        SELECT j.agent_id::text || ' / ' || j.skill_id::text
          FROM agent_skills j
          JOIN agents  p ON p.id = j.agent_id
          JOIN skills  c ON c.id = j.skill_id
         WHERE p.tenant_id <> c.tenant_id
           AND c.is_builtin IS NOT TRUE
         LIMIT 20
        """,
    ),
    (
        "agent_tools",
        """
        SELECT j.agent_id::text || ' / ' || j.tool_id::text
          FROM agent_tools j
          JOIN agents p ON p.id = j.agent_id
          JOIN tools  c ON c.id = j.tool_id
         WHERE p.tenant_id <> c.tenant_id
           AND c.is_builtin IS NOT TRUE
         LIMIT 20
        """,
    ),
    (
        "team_members",
        """
        SELECT j.team_id::text || ' / ' || j.agent_id::text
          FROM team_members j
          JOIN teams  p ON p.id = j.team_id
          JOIN agents c ON c.id = j.agent_id
         WHERE p.tenant_id <> c.tenant_id
           AND c.scope <> 'global_builtin'
         LIMIT 20
        """,
    ),
    (
        "task_dependencies",
        """
        SELECT j.task_id::text || ' / ' || j.depends_on_task_id::text
          FROM task_dependencies j
          JOIN tasks p ON p.id = j.task_id
          JOIN tasks c ON c.id = j.depends_on_task_id
         WHERE p.tenant_id <> c.tenant_id
         LIMIT 20
        """,
    ),
)

# Lectura cross-tenant del catálogo de plataforma (SELECT-only). Ver docstring.
_BUILTIN_READ_POLICIES: tuple[tuple[str, str], ...] = (
    (
        "agent_skills",
        "EXISTS (SELECT 1 FROM agents a"
        " WHERE a.id = agent_skills.agent_id AND a.scope = 'global_builtin')",
    ),
    (
        "agent_tools",
        "EXISTS (SELECT 1 FROM agents a"
        " WHERE a.id = agent_tools.agent_id AND a.scope = 'global_builtin')",
    ),
    (
        "team_members",
        "EXISTS (SELECT 1 FROM teams t WHERE t.id = team_members.team_id AND t.is_builtin IS TRUE)",
    ),
)


def _trigger_function_sql(table: str, parent_table: str, fk_column: str) -> str:
    """Cuerpo del trigger que deriva `tenant_id` del padre.

    SQL estático (una función por tabla, sin `EXECUTE format(...)`): el coste son
    unas líneas más y la ganancia es que no hay SQL dinámico que auditar dentro
    de un camino de escritura crítico para el aislamiento.
    """
    extra = ""
    if table == "task_dependencies":
        extra = """
      SELECT d.tenant_id INTO dep_tenant FROM tasks d WHERE d.id = NEW.depends_on_task_id;
      IF dep_tenant IS NULL OR dep_tenant <> parent_tenant THEN
        RAISE EXCEPTION
          'task_dependencies: dependencia cross-tenant rechazada'
          ' (task % del tenant %, depends_on % del tenant %)',
          NEW.task_id, parent_tenant, NEW.depends_on_task_id, dep_tenant
          USING ERRCODE = '23514';
      END IF;
"""
    declare_dep = "      dep_tenant uuid;\n" if table == "task_dependencies" else ""
    return f"""
    CREATE OR REPLACE FUNCTION {table}_set_tenant_id() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
      parent_tenant uuid;
{declare_dep}    BEGIN
      SELECT p.tenant_id INTO parent_tenant
        FROM {parent_table} p WHERE p.id = NEW.{fk_column};
      IF parent_tenant IS NULL THEN
        -- No existe, o la RLS del padre lo esconde de esta sesión. Ambas cosas
        -- deben cerrar la puerta: la FK se comprueba como propietario e ignora
        -- la RLS, así que sin esto una sesión podría colgar una fila de un
        -- padre que ni siquiera puede ver.
        RAISE EXCEPTION
          '{table}: {parent_table}.id = % no es visible en esta sesión;'
          ' tenant_id no se puede derivar',
          NEW.{fk_column}
          USING ERRCODE = '42501';
      END IF;
      IF NEW.tenant_id IS NOT NULL AND NEW.tenant_id <> parent_tenant THEN
        RAISE EXCEPTION
          '{table}: tenant_id % contradice el del padre {parent_table} (%)',
          NEW.tenant_id, parent_tenant
          USING ERRCODE = '23514';
      END IF;
{extra}      NEW.tenant_id := parent_tenant;
      RETURN NEW;
    END
    $$
    """


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Pre-check: nunca hacer backfill sobre datos incoherentes. --------
    for table, query in _CONSISTENCY_CHECKS:
        offenders = [row[0] for row in bind.execute(sa.text(query))]
        if offenders:
            raise RuntimeError(
                f"migración 0124 abortada: {table} contiene filas cuyo padre e hijo"
                f" pertenecen a tenants distintos, así que el backfill de tenant_id"
                f" tendría que INVENTARSE un valor. Primeras filas ofensivas"
                f" (máx. 20): {offenders}. Bórralas o re-apúntalas antes de migrar."
            )

    # --- 2. Columna + backfill + NOT NULL + índice ---------------------------
    for table, parent_table, fk_column in _JUNCTIONS:
        op.add_column(
            table,
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.execute(
            f"UPDATE {table} j SET tenant_id = p.tenant_id"
            f"   FROM {parent_table} p WHERE p.id = j.{fk_column}"
        )
        # Huérfanas imposibles (FK NOT NULL + ON DELETE CASCADE), pero si el
        # UPDATE dejase algún NULL el ALTER de abajo lo delataría igualmente.
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    # --- 3. Trigger que DERIVA tenant_id del padre --------------------------
    for table, parent_table, fk_column in _JUNCTIONS:
        op.execute(_trigger_function_sql(table, parent_table, fk_column))
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_tenant_id"
            f" BEFORE INSERT OR UPDATE ON {table}"
            f" FOR EACH ROW EXECUTE FUNCTION {table}_set_tenant_id()"
        )

    # --- 4. RLS: ENABLE + FORCE + policy de aislamiento ---------------------
    for table, _parent, _fk in _JUNCTIONS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL"
            " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
            " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )

    # --- 5. Lectura del catálogo de plataforma (fork / adopción) ------------
    for table, predicate in _BUILTIN_READ_POLICIES:
        op.execute(f"CREATE POLICY {table}_builtin_read ON {table} FOR SELECT USING ({predicate})")


def downgrade() -> None:
    """Deshace de verdad: policies, RLS, triggers, funciones, índice y columna.

    El único dato que se pierde es el `tenant_id` denormalizado, que es
    íntegramente derivable del padre (por eso lo puebla un trigger). Las filas
    de unión quedan intactas.
    """
    for table, _predicate in reversed(_BUILTIN_READ_POLICIES):
        op.execute(f"DROP POLICY IF EXISTS {table}_builtin_read ON {table}")

    for table, _parent, _fk in reversed(_JUNCTIONS):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_set_tenant_id ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_set_tenant_id()")
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")
