# Plan 15 — tests humanos

Esta guía cubre los **5 tests humanos** del Plan 15 (Instalador,
Endurecimiento y Producción). Validan lo que no se puede automatizar sin
máquinas vírgenes y un auditor: que la **instalación desde cero por
wizard** funciona en una Ubuntu nueva, que el **modo CLI desatendido**
arranca sin intervención, que el **pentest interno** confirma aislamiento
robusto, que la **reinstalación preserva datos**, y que la
**documentación es navegable** para un desarrollador nuevo.

> **Estado del plan**: `in_progress`. 27 de las 29 tareas construibles
> están completas y verdes (wizard de 9 pasos, validación de
> prerequisitos, generadores de compose/.env/config, bootstrap de Vault,
> CLI desatendido + perfiles YAML, uninstall/reinstall, pentest interno,
> seccomp/AppArmor, rotación de credenciales, hardening del panel admin,
> 6 runbooks, portal de desarrollador, smoke tests). **Restan SOLO las 2
> tareas reservadas al humano**: `task_15_27` **pentest externo**
> (auditoría profesional) y `task_15_29` **release v1.0.0** — ambas
> human-owned, no automatizables por la plataforma. Estos 5 tests humanos
> y esas 2 tareas son el último paso antes de pasar a `completed`.

> **Reservado al humano (no lo hace la plataforma)**:
>
> - `task_15_27` — **Pentest externo (auditoría profesional)**: lo
>   ejecuta un auditor externo; el entregable es
>   `docs/05-architecture-decisions/0099-external-pentest-results.md`. El
>   `human_15_03` de abajo es el **pentest interno** de aislamiento (parte
>   de la fase), distinto del externo profesional.
> - `task_15_29` — **Release v1.0.0**: el tag `v1.0.0` lo crea el operador
>   (devops) tras pasar todo lo anterior, incluido el gate full-plan
>   (override humano del `blocking_plan`).

## TL;DR

No hay `setup_demo_15.py` ni launcher dedicado: por su naturaleza estos
tests se ejecutan **sobre máquinas/VM vírgenes** (instalar desde cero,
modo CLI, reinstalar) y con un auditor (pentest interno). El stack dev de
desarrollo se levanta como siempre:

```powershell
.\scripts\dev\up.ps1     # entorno de desarrollo local (no es la instalación de producción)
```

Para los tests de instalación reales se usa el **instalador** del repo
(no el dev-stack):

```bash
# En la máquina/VM virgen, tras clonar el repo:
./scripts/install.sh                       # modo wizard (sirve UI temporal autodestructiva)
./scripts/install.sh --config install.yaml  # modo CLI desatendido (task_15_10)
./scripts/uninstall.sh                      # desinstalación con doble confirmación (task_15_12)
```

Las plantillas YAML por perfil están en `scripts/install-profiles/`
(minimal, recommended, gpu — `task_15_11`). Los runbooks operativos viven
en `docs/06-runbooks/` y el portal de desarrollador en la doc pública.

## Pre-requisitos

| Requisito                                            | Por qué                                                     |
| ---------------------------------------------------- | ----------------------------------------------------------- |
| Máquina/VM **Ubuntu 24.04 virgen**                   | `human_15_01` instala desde cero por wizard                 |
| Una **segunda** VM virgen                            | `human_15_02` prueba el modo CLI desatendido en limpio      |
| Docker + Compose v2 en las VMs                       | El instalador valida prerequisitos y levanta el stack       |
| (Opcional) GPU NVIDIA en una VM                      | `human_15_01` valida la detección de GPU si la hay          |
| Un **auditor de seguridad** (puede ser interno)      | `human_15_03` intenta escapes/escalada/fugas cross-tenant   |
| Una instalación con datos para reinstalar            | `human_15_04` reinstala con preservación de datos           |
| Un desarrollador "nuevo" (sin contexto previo)       | `human_15_05` valida que el portal/doc es navegable de cero |
| Las unseal keys de Vault guardadas (mostradas 1 vez) | Sin ellas no se recupera Vault tras reinstalar/restaurar    |

---

## `human_15_01` — Instalación desde cero en máquina virgen

**Qué prueba**: en una Ubuntu 24.04 nueva, clonar el repo y ejecutar
`install.sh` levanta el wizard en el navegador en <30 s, cada paso es
comprensible sin haber leído la doc, la detección de GPU funciona si la
hay, el panel admin queda accesible con las credenciales mostradas, y el
contenedor installer se autodestruye.

**Precondiciones**:

- Una VM Ubuntu 24.04 virgen con Docker + Compose v2.
- El repo clonado en la VM.
- (Opcional) una GPU NVIDIA para validar la detección.

**Pasos**:

1. En la VM virgen, clona el repo y lanza **`./scripts/install.sh`**
   (modo wizard).
2. Abre el navegador: el **wizard debe aparecer en menos de 30 s**.
3. Recorre los **9 pasos** (Bienvenida → Config básica → Recursos/GPU →
   Almacenamiento → Providers LLM → Tenant inicial → Resumen →
   Instalación → Listo): cada paso debe ser **comprensible para alguien
   que no ha leído la doc**.
4. En el paso de Recursos/GPU, si la máquina tiene GPU NVIDIA, la
   **detección de GPU** debe reconocerla.
5. Tras instalar, accede al **panel admin** con las **credenciales
   mostradas** en el último paso (mostradas UNA vez — anótalas).
6. Comprueba que el **contenedor installer se autodestruye** tras
   completar (`docker ps` no lo muestra).

**Resultado esperado**: el wizard aparece <30 s, los 9 pasos son
comprensibles, la GPU se detecta si la hay, el panel admin es accesible
con las credenciales mostradas y el installer se autodestruye.

**Checklist**:

- [ ] Wizard aparece en navegador en menos de 30 s.
- [ ] Cada paso del wizard es comprensible para alguien que no ha leído
      la doc.
- [ ] Detección de GPU funciona si la máquina tiene una.
- [ ] Tras instalar, panel admin accesible con credenciales mostradas.
- [ ] Contenedor installer se autodestruye.

**Pitfalls conocidos**:

- El installer corre en un **contenedor separado autodestructivo**
  (Decisión Clave): si tras completar sigue vivo, revisa el paso de
  finalización (`task_15_06`).
- Las **unseal keys de Vault se muestran UNA vez sin recuperación**
  (Decisión Clave): guárdalas en ese momento o tendrás que reinstalar.
- En máquinas con **configuraciones exóticas** el wizard puede fallar la
  validación de prerequisitos (riesgo identificado): lee el mensaje de
  error explícito; el modo CLI manual cubre casos avanzados.

---

## `human_15_02` — Modo CLI desatendido funciona

**Qué prueba**: en otra VM virgen, `install.sh --config install.yaml`
arranca el sistema sin intervención humana, las plantillas YAML por
perfil cubren los 3 casos típicos, los logs indican el progreso, y tras
completar el sistema responde igual que en la instalación con UI.

**Precondiciones**:

- Una segunda VM virgen con Docker + Compose v2.
- Un `install.yaml` (puedes partir de una plantilla de
  `scripts/install-profiles/`).

**Pasos**:

1. En la VM virgen, lanza **`./scripts/install.sh --config
install.yaml`** (modo desatendido).
2. Comprueba que, **sin intervención humana**, el sistema **arranca** por
   completo.
3. Verifica las **plantillas YAML por perfil** en
   `scripts/install-profiles/`: deben cubrir los **3 casos típicos**
   (minimal, recommended, gpu).
4. Sigue los **logs**: deben indicar **claramente el progreso** de la
   instalación.
5. Tras completar, comprueba que el sistema **responde como en la
   instalación con UI** (panel admin accesible, servicios arriba).

**Resultado esperado**: el sistema arranca sin intervención, los 3
perfiles YAML están, los logs son claros y el resultado es equivalente a
la instalación por wizard.

**Checklist**:

- [ ] Sin intervención humana, el sistema arranca.
- [ ] Las plantillas YAML por perfil cubren los 3 casos típicos.
- [ ] Logs claros indican el progreso.
- [ ] Tras completar, el sistema responde como en instalación con UI.

**Pitfalls conocidos**:

- El test automático `auto_15_11_a` exige **≥3 perfiles** en
  `scripts/install-profiles/`: si falta alguno, el modo CLI no cubre los
  3 casos.
- En desatendido no hay quien anote las **unseal keys**: el
  `install.yaml`/CLI debe persistirlas donde el operador las recupere;
  confírmalo en los logs (sin filtrarlas a stdout en claro de forma
  insegura).
- Si el desatendido se cuelga, suele ser **validación de prerequisitos**
  fallando en silencio: corre antes el paso de prerequisitos
  (`task_15_02`) para ver el diagnóstico.

---

## `human_15_03` — Pentest interno: aislamiento robusto

**Qué prueba**: un auditor intenta escapes de contenedor, escalada de
privilegios y fugas cross-tenant, y confirma que ninguno tiene éxito, que
el rate limiting resiste un DDoS básico, y que las credenciales rotadas
no quedan en logs.

**Precondiciones**:

- Una instalación completa (de `human_15_01`/`02`).
- Un auditor de seguridad (puede ser interno) con acceso controlado.
- seccomp/AppArmor aplicados (`task_15_15`/`task_15_16`) y rotación de
  credenciales activa (`task_15_17`).

> Este es el **pentest interno** de aislamiento (parte de la fase). El
> **pentest externo profesional** (`task_15_27`) es una tarea aparte,
> reservada a un auditor externo, cuyo entregable es el ADR
> `0099-external-pentest-results.md` — no se cubre con este test humano.

**Pasos**:

1. El auditor intenta **escapes de contenedor** desde los runtimes
   efímeros (agent/test/review): **ninguno** debe tener éxito (cap-drop
   ALL, sin socket Docker, seccomp/AppArmor).
2. Intenta **escalada de privilegios** dentro de los contenedores: debe
   fallar.
3. Intenta **acceso cross-tenant no autorizado** (leer datos de otro
   tenant vía API/DB): **ningún** acceso cross-tenant debe prosperar
   (RLS + middleware tenant).
4. Lanza un **DDoS básico** contra los endpoints: el **rate limiting**
   debe resistir.
5. Revisa los **logs** tras una rotación de credenciales: las
   **credenciales rotadas NO deben quedar en logs**.

**Resultado esperado**: ningún escape ni escalada, ningún acceso
cross-tenant, el rate limiting aguanta el DDoS básico, y las credenciales
rotadas no aparecen en logs.

**Checklist**:

- [ ] Ningún escape de contenedor exitoso.
- [ ] Ningún cross-tenant unauthorized access.
- [ ] Rate limiting resiste DDoS básico.
- [ ] Credenciales rotadas no quedan en logs.

**Pitfalls conocidos**:

- Los **workers NO ejecutan código del usuario** (principio rector):
  lanzan runtimes efímeros con red restringida. Un escape debe atacar el
  runtime, no el worker — confírmalo con el modelo de amenazas.
- La **rotación de credenciales** usa Vault dynamic secrets
  (`task_15_17`): si una credencial rotada aparece en logs, es un
  hallazgo a documentar en el pentest interno (`tests/security/`).
- El pentest interno **alimenta** al externo (`task_15_27`): documenta
  hallazgos en `tests/security/test_pentest_findings.py` y el ADR, no
  solo "pasa/falla".

---

## `human_15_04` — Reinstalación sobre datos existentes

**Qué prueba**: tras instalar y llenar de datos, reinstalar con
"preservar datos" deja los datos persistentes intactos, regenera solo la
configuración (limpia), y los tenants existentes pueden hacer login con
sus credenciales previas.

**Precondiciones**:

- Una instalación con datos (tenants, proyectos, usuarios) ya creados.
- Las unseal keys de Vault guardadas.

**Pasos**:

1. Sobre una instalación con datos, **reinstala con la opción "preservar
   datos"** (`task_15_13`).
2. Tras reinstalar, comprueba que los **datos persistentes están
   intactos** (proyectos, planes, conversaciones, KBs).
3. Verifica que **solo la configuración se ha regenerado** (limpia):
   docker-compose.yml/.env/config nuevos, datos viejos.
4. **Login** con credenciales de **tenants existentes previas a la
   reinstalación** → deben funcionar.

**Resultado esperado**: los datos persistentes sobreviven, solo la config
se regenera, y los tenants loguean con sus credenciales previas.

**Checklist**:

- [ ] Datos persistentes intactos tras reinstalación.
- [ ] Solo configuración se regenera (limpia).
- [ ] Tenants existentes pueden hacer login con sus credenciales previas.

**Pitfalls conocidos**:

- **Vault necesita unseal** tras la reinstalación: usa las unseal keys
  guardadas (runbook `docs/06-runbooks/05-key-rotation.md`). Sin unseal,
  los secretos no se leen y el login puede fallar.
- Si el login falla pese a preservar datos, comprueba que el **secreto de
  firma JWT** sobrevivió (vive en Vault): un secreto distinto invalida
  tokens activos, pero el login con password debería seguir — re-login.
- "Preservar datos" preserva los **volúmenes** (postgres, MinIO, Vault,
  Redis); si reinstalaste sin marcar la opción, los datos se pierden — es
  irreversible.

---

## `human_15_05` — Documentación es navegable

**Qué prueba**: un desarrollador nuevo lee el portal de desarrollador y
completa el Quick Start en <30 min, encuentra una API Reference completa
y precisa, SDKs documentados con ejemplos, y runbooks que cubren los
escenarios operativos típicos.

**Precondiciones**:

- El portal de desarrollador desplegado (`task_15_25`).
- Un desarrollador "nuevo" sin contexto previo del producto.

**Pasos**:

1. El desarrollador nuevo abre el **portal de desarrollador** y sigue el
   **tutorial Quick Start**: debe completarlo en **menos de 30 min**.
2. Consulta la **API Reference**: debe ser **completa y precisa** (casa
   con `/api/v1/openapi.json` del Plan 13).
3. Revisa los **SDKs** (Python/TS): documentados con **ejemplos**
   ejecutables.
4. Hojea los **runbooks** (`docs/06-runbooks/`): deben cubrir los
   **escenarios operativos típicos** (instalación, troubleshooting,
   upgrade, DR, rotación de keys, capacity).

**Resultado esperado**: Quick Start completable en <30 min, API Reference
completa y precisa, SDKs con ejemplos, y runbooks que cubren lo
operativo.

**Checklist**:

- [ ] Tutorial Quick Start completable en menos de 30 min.
- [ ] API Reference completa y precisa.
- [ ] SDKs documentados con ejemplos.
- [ ] Runbooks cubren los escenarios operativos típicos.

**Pitfalls conocidos**:

- El portal de desarrollador y la API Reference se apoyan en el **Plan 13**
  (OpenAPI 3.1 + SDKs): si el Quick Start falla en la parte de API,
  comprueba que `/api/v1/openapi.json` responde.
- Los runbooks consolidan piezas de otras fases (DR del Plan 12, key
  rotation): si un runbook referencia algo no instalado en tu entorno de
  prueba, valida la navegabilidad, no la ejecución de cada paso.

---

## Cierre del plan

Tras pasar los 5 tests humanos y completar las 2 tareas reservadas al
humano (`task_15_27` pentest externo + `task_15_29` release v1.0.0):

1. Edita `docs/roadmap/15-instalador-produccion.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica el ADR del pentest externo
   `docs/05-architecture-decisions/0099-external-pentest-results.md`, la
   entrada en
   [`docs/07-changelog/15-instalador-produccion.md`](../../07-changelog/)
   y los runbooks en [`docs/06-runbooks/`](../../06-runbooks/).
3. Verifica que el tag **`v1.0.0`** está creado y que el PR
   `plan/15-instalador-produccion` está mergeado a `master`.

## Troubleshooting

| Síntoma                                         | Causa probable                                       | Fix                                                                    |
| ----------------------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------- |
| El wizard no aparece tras `install.sh`          | Validación de prerequisitos fallida o puerto ocupado | Lee el mensaje de error del paso 1; revisa Docker/Compose v2 en la VM  |
| El installer no se autodestruye                 | Paso de finalización (`task_15_06`) no completó      | Revisa los logs del contenedor installer; `docker compose down` manual |
| El modo CLI se cuelga sin mensaje               | Prerequisito fallando en silencio                    | Corre el paso de prerequisitos aparte para ver el diagnóstico          |
| Login falla tras reinstalar con preservar datos | Vault sin unseal o secreto JWT no recuperado         | Unseal con las unseal keys (runbook 05); re-login con password         |
| Datos perdidos tras reinstalar                  | No se marcó "preservar datos"                        | Irreversible; restaura desde backup (Plan 12)                          |
| Una credencial rotada aparece en logs           | Hallazgo de seguridad del pentest interno            | Documéntalo en `tests/security/` + el ADR; no debería ocurrir          |

Errores transversales viven en `docs/03-guides/gotchas/`.
