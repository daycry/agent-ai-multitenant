---
title: Comandos del stack y runtime por proyecto (PHP, Node, .NET, Python)
audience: tenant admin, project owner
phase: 06.16-polyglot-tool-catalog
updated: 2026-06-01
---

# Comandos del stack y runtime por proyecto

Esta guía explica cómo dejar que un agente **lance comandos del stack del
proyecto** (`php`, `composer`, `vendor/bin/phpunit`, `pest`, `npm`,
`dotnet`…) y cómo elegir en qué **runtime template** corren los tests del
proyecto. Es el flujo que convierte un proyecto Python en uno de
cualquier stack.

> **TL;DR**: en `/admin/projects/{id}` → **Comandos & runtime**: pulsa el
> **preset** de tu stack (PHP / Node / .NET / Python) para rellenar los
> **chips** de comandos autorizados, elige el **runtime por defecto** del
> proyecto, y guarda. Luego asigna la tool **`shell_exec`** al agente
> (sección "Tools del agente", aparece en **Básicas** con badge
> **Privilegiada**). El agente sólo podrá ejecutar los binarios que hayas
> autorizado; **una lista vacía no deja ejecutar nada**.

## Las dos piezas

| Pieza                    | Qué controla                                                                                  | Campo del proyecto         |
| ------------------------ | --------------------------------------------------------------------------------------------- | -------------------------- |
| **Comandos autorizados** | Qué binarios puede ejecutar `shell_exec` en este proyecto. **Deny-by-default**: vacío = nada. | `allowed_commands`         |
| **Runtime por defecto**  | En qué runtime template corren los `run_*` (tests/lint/typecheck/build) del proyecto.         | `default_runtime_template` |

Ambos son **por proyecto** y **tenant-scoped** (la RLS de `projects` ya
los aísla). Sólo un `tenant_admin` puede editarlos; un miembro sin rol
admin los ve en modo lectura.

## 1. Autorizar comandos (chips + presets)

En `/admin/projects/{id}` → **Comandos & runtime**, la allowlist se
edita como **chips**:

- **Preset por stack**: pulsa **PHP**, **Node**, **.NET** o **Python** y
  los chips se rellenan con los binarios típicos de ese stack (sin
  pisar lo que ya hubiera):
  - **PHP** → `php`, `composer`, `vendor/bin/phpunit`, `pest`
  - **Node** → `npm`, `npx`, `node`
  - **.NET** → `dotnet`
  - **Python** → `python`, `pytest`
- **Añadir uno a mano**: escribe el comando y pulsa **+** (o Enter).
- **Quitar**: la **✕** de cada chip.
- **Guardar**: persiste vía `PUT /projects/{id}`.

### Cómo se valida un comando (importante)

`shell_exec` recibe un comando completo (p.ej. `composer install`), lo
parsea con `shlex` en un **argv** (nunca a través de una shell → sin
inyección) y comprueba el **basename** del primer token contra la
allowlist:

- `composer install` → basename `composer` → permitido si `composer`
  está en los chips.
- `vendor/bin/phpunit --filter Foo` → basename `phpunit` → autoriza con
  el chip `phpunit` (o el preset PHP, que incluye `vendor/bin/phpunit`,
  cuyo basename es `phpunit`).
- `rm -rf /` → basename `rm` → **rechazado** (no está en la allowlist);
  el error devuelve la lista de comandos permitidos.

Además: el comando corre con **timeout** (se mata si se cuelga), su
salida se **trunca** si es enorme, y un `cwd` que intente salir del
`/workspace` se **rechaza**. La barrera real de aislamiento la pone el
contenedor (sin socket Docker, red restringida, cap-drop ALL); la
allowlist es una **segunda barrera** explícita.

> **Deny-by-default**: si dejas la lista **vacía**, `shell_exec` existe
> pero **rechaza todo**. Hay que autorizar conscientemente cada binario.

## 2. Elegir el runtime por defecto del proyecto

En la misma sección, el selector **Runtime por defecto** fija
`default_runtime_template`. Es el runtime template (imagen) en el que
corren los `run_*` del proyecto. Opciones (espejan
`shared_test_runtimes.catalog`):

`python-pytest`, `php-phpunit`, `php-pest`, `dotnet-test`, `node-jest`,
`node-vitest`, `node-playwright`, `go-test`, `java-gradle`, `java-maven`,
`ruby-rspec`, `rust-cargo`, `generic-shell`, `generic-http`.

**Cómo se resuelve** (precedencia):

1. **`default_runtime_template` del proyecto** si lo fijas → un proyecto
   PHP con `php-phpunit` corre sus `run_*` ahí, **no** en `python-pytest`.
2. el **default propio del tool** (p.ej. `run_pytest` → `python-pytest`)
   si el proyecto **no fija** runtime (lo dejas en _"sin runtime por
   defecto"_).
3. `python-pytest` como **último fallback** para los `run_*` que no traen
   uno propio (`run_lint` / `run_typecheck` / `run_build`).

> **Backward-compatible**: si no eliges runtime (campo vacío), los
> proyectos Python siguen corriendo `run_pytest` en `python-pytest`
> exactamente como antes. Un runtime id desconocido da un **error claro**
> (con el listado de los conocidos), no un crash.

## 3. Asignar `shell_exec` al agente

Autorizar comandos **no basta**: el agente necesita además tener la tool
`shell_exec` asignada. En `/admin/agents/{id}` → **Tools del agente**
(ver [Asignar tools a un agente](./asignar-tools-a-agentes.md)):

- `shell_exec` aparece en la pestaña **Básicas** (es `is_builtin=true`)
  con un badge **Privilegiada** (`security_level=privileged`). Que sea
  básica y a la vez privilegiada es correcto: el nivel de seguridad es un
  eje **ortogonal** a básica/avanzada (ver
  [ADR 0044](../05-architecture-decisions/0044-per-agent-tool-assignment-y-taxonomia-derivada.md)
  y [ADR 0045](../05-architecture-decisions/0045-comandos-shell-por-proyecto-y-runtime-por-stack.md)).
- Actívala y guarda.

A partir de ahí el agente puede `shell_exec` los binarios autorizados del
proyecto, y sus `run_*` usan el runtime que elegiste.

## Ejemplo completo: un proyecto PHP

1. **Comandos & runtime** → preset **PHP** → chips `php`, `composer`,
   `vendor/bin/phpunit`, `pest`.
2. Runtime por defecto = **`php-phpunit`** → guardar.
3. **Tools del agente** → activar `shell_exec` (Básicas / Privilegiada) →
   guardar.
4. El agente ejecuta `composer install` y `vendor/bin/phpunit` → corren
   dentro del runtime PHP.
5. El agente intenta `rm -rf /` o un binario no autorizado → **rechazado**
   (no está en la allowlist).
6. Los `run_*` del agente usan **`php-phpunit`**, no `python-pytest`.

## Preguntas frecuentes

**¿Por qué `shell_exec` no ejecuta nada aunque la tenga asignada?**
La allowlist del proyecto (`allowed_commands`) está vacía. Añade los
binarios (o pulsa un preset) en **Comandos & runtime** y guarda.

**¿La allowlist es por ruta o por nombre?** Por **basename**: autorizar
`phpunit` permite cualquier `phpunit` del PATH dentro del sandbox; el
confinamiento real lo da el contenedor.

**¿Tengo que crear una tool por lenguaje?** No. El runtime lo fija el
**proyecto** (`default_runtime_template`); hay un único `run_pytest`,
`shell_exec`, etc. para todos los stacks.

**¿Esto se hereda de tenant/plataforma?** No. La allowlist es **plana y
por proyecto**, explícita a propósito (es una tool privilegiada). Sin
herencia ni allowlist global.

## Referencias

- [ADR 0045 — Comandos shell por proyecto + runtime por stack](../05-architecture-decisions/0045-comandos-shell-por-proyecto-y-runtime-por-stack.md)
- [ADR 0044 — Asignación de tools por agente + taxonomía derivada](../05-architecture-decisions/0044-per-agent-tool-assignment-y-taxonomia-derivada.md)
- [Asignar tools a un agente (básicas vs avanzadas)](./asignar-tools-a-agentes.md)
- Plan: `docs/roadmap/06.16-polyglot-tool-catalog.md`
- Changelog: `docs/07-changelog/06.16-polyglot-tool-catalog.md`
