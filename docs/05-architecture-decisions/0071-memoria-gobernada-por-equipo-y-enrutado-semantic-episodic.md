---
adr_id: "0071"
title: "Política de memoria gobernada por el equipo + enrutado semantic/episodic"
status: accepted
date: 2026-06-19
authors: [system_architect]
plan_referenced: personalizacion-equipos-built-in
docs_language: es
extends: ["0055", "0059", "0065"]
---

# ADR 0071 — Memoria gobernada por el equipo + enrutado semantic/episodic

> **Estado: `accepted`** (operador, 2026-06-19). El control `memory_scope`
> por-agente se **deshabilita** cuando el agente es miembro de ≥1 equipo (nota:
> "se gestiona desde el/los equipo(s)").

## Contexto

Dos carencias del modelo de memoria actual (ADR 0055/0059):

1. **El `memory_scope` es solo por agente.** La cadena es
   `memory.default_scope` (plataforma) → agente. No hay nivel de **equipo** ni de
   **proyecto**. Como el modelo (`model_config`) **sí** se hereda
   `plataforma → proyecto → equipo → agente` (ADR 0065), la memoria es la
   excepción incoherente: un equipo compartido entre proyectos puede acabar con
   miembros con scopes distintos → memoria del equipo **fragmentada**, y alguien
   puede romper la coherencia editando un solo miembro.
2. **Un único scope para TODA la memoria de un agente.** El memorizer ya clasifica
   cada memoria como **`semantic`** (regla/lección generalizable) o **`episodic`**
   (evento concreto), pero la persistencia aplica el mismo scope a las dos. No se
   puede "compartir la pericia y mantener lo puntual en su proyecto".

Objetivo del operador: que un **equipo** usado en varios proyectos **comparta su
pericia** (lecciones, errores a no repetir, forma de trabajar) entre proyectos,
manteniendo lo específico de cada proyecto en su sitio, y que la política sea
**del equipo**, no algo que se fragmente por agente.

## Decisión

### 1) La política de memoria la gobierna el equipo del contexto de ejecución

Campo nuevo **`Team.memory_scope`** (nullable). Resolución del scope **efectivo**
en cada ejecución (un agente ejecuta una tarea de un proyecto):

```
scope_efectivo =
    project.team.memory_scope        si el proyecto tiene equipo y este fija política
    else  agent.memory_scope          (agente sin equipo / proyecto sin equipo)
    else  memory.default_scope        (default de plataforma)
```

El equipo relevante es **el del proyecto de la tarea** (`project.team_id`) — el
mismo que ya resuelve `team_shared` hoy —, así que **no importa en cuántos
equipos esté el agente**: manda el equipo del contexto. El `memory_scope`
por-agente queda como **fallback sin equipo**.

- **Retro-compatible**: `Team.memory_scope` nullable; si no se fija, la
  resolución cae al `memory_scope` del agente (comportamiento actual). Fijar la
  política del equipo es opt-in por equipo.
- **UI**: selector "Política de memoria del equipo" en la ficha del equipo. En la
  ficha del agente, el selector de `memory_scope` se **deshabilita siempre que el
  agente sea miembro de ≥1 equipo**, con la nota _"Se gestiona desde el equipo
  {X}"_ (o _"desde los equipos {X, Y}"_ si pertenece a varios). El control
  por-agente **solo se habilita cuando el agente no pertenece a ningún equipo**.
  (Matiz: el _disable_ depende de la **pertenencia** a equipo — uno o varios; el
  scope **efectivo** en una ejecución concreta lo decide el equipo del contexto
  de esa ejecución, `project.team_id`. Para `project_local` coinciden.)
  (NO se mueve la gestión del agente dentro del equipo ni se ocultan agentes de la
  pantalla de Agentes — fuera de alcance.)

### 2) Enrutado por tipo: semantic viaja, episodic se queda

Sobre el `scope_efectivo` **R**, cada memoria se persiste según su `type`
(distinción clásica memoria semántica vs episódica, alineada con mem0 / ADR 0059):

| `type`                       | scope al persistir         | racional                                      |
| ---------------------------- | -------------------------- | --------------------------------------------- |
| `semantic` (lección/regla)   | **R**                      | la pericia viaja al alcance del equipo/agente |
| `episodic` (evento concreto) | **min(R, project_shared)** | el hecho puntual se queda en su proyecto      |

Orden de scopes: `private < project_shared < team_shared < global`. Por tanto:

| R                | semantic       | episodic       |
| ---------------- | -------------- | -------------- |
| `global`         | global         | project_shared |
| `team_shared`    | team_shared    | project_shared |
| `project_shared` | project_shared | project_shared |
| `private`        | private        | private        |

**Automático** (no opt-in): el comportamiento viejo (un episódico puntual
contaminando el pool de equipo/global) no lo quiere nadie; no es destructivo
(solo cambia el enrutado de memorias **nuevas**; las ya guardadas conservan su
scope; sin migración de datos). Salvaguarda: si el clasificador no da un `type`
claro, se trata como `episodic` (el scope más estrecho — nunca sobre-comparte).

`episodic → project_shared` requiere `project_id`; las ejecuciones de agente
siempre tienen proyecto. Si faltara (caso degenerado), el episódico cae a R.

## Alternativas

- **Mantener scope por-agente con override** (mi propuesta inicial): rechazada por
  el operador — fragmenta la memoria del equipo; la pertenencia a equipo debe
  gobernar sin override mientras el agente esté en el equipo.
- **Restringir un agente a un solo equipo**: innecesario — el equipo del contexto
  de ejecución (`project.team_id`) ya desambigua la memoria.
- **Split semantic/episodic opt-in (flag)**: rechazada — deuda de configuración
  para un comportamiento que es estrictamente mejor.

## Consecuencias

- **+** Un equipo de tenant en varios proyectos **acumula y comparte su pericia**
  (semantic) entre ellos; lo puntual (episodic) queda por proyecto. Coherencia
  garantizada por el equipo, no fragmentable por agente.
- **+** Consistente con la herencia de `model_config` (ADR 0065); la memoria deja
  de ser la excepción.
- **+** Retro-compatible: sin política de equipo y sin tocar tipos previos, el
  comportamiento no cambia salvo el enrutado por tipo (mejora, no destructiva).
- **−** El `memory_scope` por-agente pierde efecto cuando un equipo gobierna
  (queda como fallback sin equipo) — se comunica en la UI.

## Tests

- Resolución del scope efectivo: equipo del proyecto > agente > default de
  plataforma; equipo sin política cae al agente; sin equipo usa el del agente.
- Enrutado por tipo: para cada R, `semantic→R` y `episodic→min(R, project_shared)`;
  `type` ausente → episodic (estrecho).
- Persistencia: el puntero correcto (team_id/project_id/user_id) se setea según el
  scope resultante (CHECK `ck_memory_entries_scope_pointer`).
- Retro-compat: `Team.memory_scope` nullable → comportamiento previo.
