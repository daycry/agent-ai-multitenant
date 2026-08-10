---
title: Triage de vulnerabilidades y política de excepciones
docs_language: es
audience: operador, tech lead, desarrollador
updated: 2026-08-02
---

# Triage de vulnerabilidades y política de excepciones

**Cuándo usarlo**: cuando el job `SCA (pip-audit + npm audit)` o un paso Trivy
sale en rojo, cuando llega un PR de Dependabot, o cuando toca la revisión
periódica de las excepciones.

Este runbook es la contraparte humana del plan
[`prod-11-cadena-suministro`](../roadmap/prod-11-cadena-suministro.md). Los
escáneres detectan; **quien decide qué hacer es una persona**, y sin criterio
escrito un gate SCA muere en dos semanas por fatiga de alertas.

---

## 1. Qué se escanea, dónde y con qué umbral

Resumen operativo. La ficha completa —con el porqué de cada umbral, el reparto
de Trivy entre los tres workflows y lo que queda deliberadamente sin pinear— está
en la referencia [cadena-suministro.md](../04-reference/cadena-suministro.md).

| Superficie                              | Herramienta                          | Dónde corre                                   | Umbral                             | Excepciones         |
| --------------------------------------- | ------------------------------------ | --------------------------------------------- | ---------------------------------- | ------------------- |
| Dependencias Python (14 distribuciones) | `pip-audit --strict --skip-editable` | `ci.yml` → job `security-scan`                | cualquier aviso conocido           | `.pip-audit-ignore` |
| npm `apps/admin-panel`                  | `npm audit --omit=dev`               | `ci.yml` → job `security-scan`                | `--audit-level=high`               | (ver §5)            |
| npm `apps/installer`                    | `npm audit --omit=dev`               | `ci.yml` → job `security-scan`                | `--audit-level=high`               | (ver §5)            |
| 5 imágenes de plataforma                | Trivy                                | `ci.yml` → job `build-images`                 | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| 14 runtime templates                    | Trivy                                | `build-runtime-templates.yml` (matriz)        | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| 5 imágenes publicables                  | Trivy                                | `release-images.yml`                          | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| Drift del lockfile                      | `uv lock --check`                    | `ci.yml` → job `lint-python` (**bloqueante**) | cualquier desincronía              | ninguna             |

Vía reactiva: [`.github/dependabot.yml`](../../.github/dependabot.yml) con
cuatro ecosistemas (pip, npm, docker, github-actions), semanal, agrupado.

> **Estado del gate a 2026-07-31**: el job `security-scan` corre con
> `continue-on-error: true` (**modo informe**). Ver §6 para lo que falta para
> convertirlo en check obligatorio.

---

## 2. Cómo leer un fallo

### `pip-audit`

Sale una tabla `Name / Version / ID / Fix Versions`. Lo importante:

- **`Fix Versions` con contenido** → hay versión parcheada: es un caso de
  actualizar (§3), no de suprimir.
- **`Fix Versions` vacío** → no hay fix upstream todavía. Valora el vector real
  (§4) antes de decidir; si el paquete no es alcanzable desde el código, es
  candidato a excepción con fecha corta.
- Si el paquete es una **transitiva**, mira quién lo arrastra:
  `pip index versions <paquete>` y `pip show <paquete>` (campo `Required-by`).
  Muchas veces basta con subir el paquete de arriba.

Reproducirlo en local, sobre el árbol exacto que CI instala:

```bash
uvx pip-audit --no-deps -r constraints.txt
```

### `npm audit`

El formato agrupa por paquete y lista los avisos con su rango afectado. Lee
**dos** cosas antes de nada:

1. **El rango afectado.** Un aviso que dice `next 9.3.4-canary.0 - 16.3.0-canary.5`
   NO se arregla subiendo dentro de la misma línea de parches.
2. **Qué propone `npm audit fix`.** Si dice `--force` y «will install X, which
   is a breaking change», entonces no hay fix compatible y el caso escala a
   decisión de producto, no de triage.

```bash
cd apps/admin-panel && npm audit --omit=dev --audit-level=high
cd apps/installer   && npm audit --omit=dev --audit-level=high
```

`--omit=dev` es deliberado: eslint/playwright/vitest **no se despliegan**, y su
ruido tapaba la señal de lo que sí llega a producción.

### Trivy

Sale una tabla por capa con `Library / Vulnerability / Severity / Installed /
Fixed Version`. Los pasos corren con `ignore-unfixed: true`, así que **todo lo
que aparece TIENE fix disponible**: es actualizable por definición. Casi
siempre la respuesta es «refrescar el digest de la base» (§3).

```bash
docker build -f docker/agent-runtimes/<slug>/Dockerfile -t tmp:scan docker/agent-runtimes/<slug>
trivy image --severity HIGH,CRITICAL --ignore-unfixed --ignorefile .trivyignore tmp:scan
```

---

## 3. El criterio por defecto es ACTUALIZAR

En orden, y no se pasa al siguiente hasta descartar el anterior:

1. **¿Hay versión parcheada dentro del rango declarado?** Actualiza y regenera
   el lock:

   ```bash
   uv lock            # respeta los rangos de los pyproject.toml
   uv export --frozen --all-packages --all-extras --all-groups \
             --no-hashes --no-emit-workspace --no-annotate --no-header \
             -o /tmp/c.txt        # y pega la cabecera de constraints.txt encima
   ```

   Para npm: `npm install <pkg>@<version>` en la superficie afectada y commit
   del `package-lock.json`.

   Para una base Docker, resuelve el digest nuevo y sustituye el `@sha256:` del
   `FROM`. Normalmente ya te lo trae un PR de Dependabot:

   ```bash
   docker buildx imagetools inspect <img>:<tag> --format '{{.Manifest.Digest}}'
   ```

2. **¿El fix exige subir de major?** Entonces no es triage: abre una tarea (o un
   ADR si rompe contrato) y **mientras tanto** documenta la excepción con fecha
   de revisión. No lo dejes sin registrar «porque ya se sabe».

3. **¿No hay fix upstream?** Excepción con justificación y fecha corta (§5), y
   revisa en la fecha.

**Nunca** se resuelve un fallo bajando el umbral del escáner ni quitando la
superficie del job. Si alguien lo intenta, las guardas de
`tests/unit/test_supply_chain_config.py` lo ponen en rojo.

---

## 4. Cómo valorar el riesgo real

El score CVSS es una entrada, no la respuesta. Tres preguntas que en este
sistema cambian la conclusión:

1. **¿El código vulnerable es alcanzable?** Una CVE en un parser de un formato
   que no procesamos no es explotable aquí.
2. **¿Dónde vive la dependencia?** Las 16 imágenes de runtime son donde el
   **Principio Rector 2** deposita el aislamiento del código NO confiable: una
   escalada local ahí importa mucho más que en una herramienta de build.
3. **¿Está expuesta a entrada de un tercero?** El admin-panel y la api-server
   reciben tráfico; el `installer` solo vive durante el bootstrap.

Sube la prioridad cuando la respuesta combine «alcanzable» + «runtime de
agentes» o «alcanzable» + «superficie HTTP».

---

## 5. Política de excepciones (formato obligatorio)

Dos ficheros versionados, y ningún otro sitio:

- [`.trivyignore`](../../.trivyignore) — imágenes.
- [`.pip-audit-ignore`](../../.pip-audit-ignore) — Python.

Cada entrada va precedida —**sin línea en blanco de por medio**— de sus
comentarios, que deben contener:

1. **Qué es** y en qué paquete/versión.
2. **Por qué no se arregla ahora** (no hay fix / el fix rompe / no alcanzable).
3. **`# review: YYYY-MM-DD`**, la fecha en la que hay que volver a mirarlo.

```text
# CVE-2025-XXXXX en libfoo 1.2: el fix está en 2.0, que cambia la ABI que usa
# el binding nativo de python3-saml. Migración en el plan prod-NN.
# review: 2026-09-30
CVE-2025-XXXXX
```

La guarda `test_sca_ignore_lists_exist_and_document_every_exception` pone en
rojo cualquier entrada sin justificación legible o sin fecha. Es a propósito:
sin fecha obligatoria una supresión es permanente de facto.

**npm no tiene mecanismo de ignore por aviso.** `npm audit` solo admite
`--audit-level`. Por eso una vulnerabilidad npm que no se puede arreglar **no
se suprime**: se registra en el plan/ADR correspondiente y el gate npm no puede
volverse obligatorio hasta resolverla (§6).

### Calendario de revisión

- **Cada lunes**, con los PRs de Dependabot: mirar las entradas cuya
  `review:` caiga en los próximos 14 días.
- **El primer lunes de cada mes**: repasar TODAS las entradas de los dos
  ficheros, aunque no venzan. Una lista de excepciones que solo crece es una
  lista que nadie lee.
- Una entrada vencida sin decisión se trata como un fallo del gate, no como
  ruido.

---

## 6. Del modo informe al gate obligatorio

`security-scan` arranca con `continue-on-error: true` a propósito
(`task_sca_gate_08`): convertirlo en gate el día 1 bloquearía todos los PRs con
el backlog heredado. Para flipearlo hacen falta **dos pasos, ambos humanos**:

1. **Vaciar o justificar el backlog.** Bloqueante conocido a 2026-07-31:
   `next` no sale limpio de `npm audit --omit=dev --audit-level=high` **ni en
   14.2.35**, que es el último parche de la línea 14.2.x. El rango de los avisos
   abarca todo 14.x (y arrastra un `postcss` empotrado) y el único fix es
   **next 16**, un salto de major con roturas. Eso necesita su propio plan.
   Mientras tanto el gate npm no puede ser obligatorio sin mentir.

   Medición del 2026-07-31 sobre `next` 14.2.35 ya instalado (`node -e` lo
   confirma en las dos superficies), para que nadie tenga que repetirla:

   | Comando                                       | admin-panel | installer  |
   | --------------------------------------------- | ----------- | ---------- |
   | `npm audit --omit=dev --audit-level=critical` | exit **0**  | exit **0** |
   | `npm audit --omit=dev --audit-level=high`     | exit **1**  | exit **1** |

   Es decir: la **crítica** que motivó el hallazgo (`GHSA-955p-x3mx-jcvp`,
   divulgación no autenticada de Server Functions) **está cerrada**; quedan 2
   avisos `high` sin fix dentro de 14.x. `npm audit fix --force` propondría
   `next@16`: no se ejecuta a ciegas.

   **Re-medido el 2026-08-01: resultado idéntico**, con npm nombrando ya la
   versión concreta (`next@16.2.12`, «which is a breaking change»). Es la tercera
   medición con el mismo resultado, así que **no vuelvas a medirla**: la
   condición de salida está escrita en
   [cadena-suministro.md §4](../04-reference/cadena-suministro.md) y es
   `--audit-level=high` en exit 0 en las dos superficies. Un parche nuevo de
   14.2.x **no la cumple** — el rango vulnerable abarca toda la línea 14.

2. **Quitar el `continue-on-error`** del job en `.github/workflows/ci.yml` y
   añadir `SCA (pip-audit + npm audit)` a los **checks requeridos de branch
   protection** (Settings → Branches → `master`). Esa lista la administra
   [`prod-02-ci-en-verde`](../roadmap/prod-02-ci-en-verde.md); este plan la
   extiende, no la redefine. Requiere permisos de administración del repo.

Verificación de que el gate hace lo que dice (test humano `human_prod11_01`):
crear una rama que degrade una dependencia a una versión vulnerable conocida y
comprobar que el PR sale en rojo y que branch protection impide mergearlo.

### Checklist del operador (2026-08-02)

Lo que sigue es **todo** lo que falta, en el orden en que hay que hacerlo. Nada de
esto lo puede hacer un agente: el paso 1 es una decisión de alcance y los pasos
3–4 exigen permisos de administración del repositorio.

- [ ] **1. Decidir qué hacer con `next`.** Es el único bloqueante técnico. Dos
      salidas honestas, y hay que elegir una:
      **(a)** abrir un plan/ADR de migración a **next 16** en el `admin-panel` y
      en el frontend del `installer` (salto de major con roturas; no cabe en una
      tarea suelta), o
      **(b)** declarar los avisos `high` como excepción razonada en la
      ignore-list de npm con `# review: YYYY-MM-DD`, igual que las de
      `.trivyignore` / `.pip-audit-ignore` (§5), asumiendo por escrito el riesgo
      residual hasta que exista (a).
      Sin (a) o (b), el gate npm nace en rojo permanente y bloquea todos los PRs. > **Medido de nuevo el 2026-08-10, y va a peor: son 3 avisos `high`, no 2.** > El rango vulnerable que reporta npm es `next 9.3.4-canary.0 –
  > 16.3.0-preview.10`, así que **ninguna versión de las líneas 14 ni 15 lo > cierra** y el destino de (a) es **`next@16.3.0`** (antes 16.2.12). Uno de > los tres llega por el `postcss`empotrado en`next`, no por `next` mismo: > si se elige (b), la excepción tiene que cubrirlo explícitamente. La > conclusión operativa no cambia —hay que elegir—, pero el coste de **no** > elegir sube en cada medición.
- [ ] **2. Quitar el modo informe.** Borrar la línea `continue-on-error: true`
      del job `security-scan` en `.github/workflows/ci.yml` (hoy en la **línea
      321**, justo bajo `timeout-minutes: 30`). Es un cambio de una línea:
      el modo está declarado explícitamente y no es un olvido — lo guarda
      `test_security_scan_declares_its_gate_mode`, y `auto_prod11_08_a`
      (`pytest tests/unit/test_supply_chain_config.py -k 'gate and not
continue_on_error'`) pasa a verde en cuanto se retira.
- [ ] **3. Añadirlo a branch protection.** GitHub → **Settings → Branches →
      `master` → Require status checks to pass** → añadir el check con su nombre
      exacto: **`SCA (pip-audit + npm audit)`** (es el `name:` del job, no el id
      `security-scan`; si se escribe el id, el check nunca casa y la protección
      queda de adorno sin avisar).
- [ ] **4. Arreglar antes la facturación de la cuenta.** CI está caído por
      «recent account payments have failed» (<https://github.com/settings/billing>).
      Un check requerido que nunca corre **bloquea todos los merges**: hacer el
      paso 3 con CI caído deja el repo sin poder integrar nada.
- [ ] **5. Verificar** con el test humano `human_prod11_01` descrito arriba.

**Lo que ya está listo para ese día** y no hay que preparar: las dos ignore-lists
existen con justificación y fecha de revisión por entrada
(`test_sca_ignore_lists_exist_and_document_every_exception`, en verde), los tres
workflows escanean sus imágenes con Trivy (24 imágenes, 9 pasos) y el lock no ha
derivado (`uv lock --check` → exit 0).

---

## 7. Flujo de los PRs de Dependabot

**Quién revisa**: el tech lead de guardia esa semana; si el PR toca las imágenes
de runtime (ecosistema `docker`), lo revisa con el responsable de plataforma.

**Qué debe pasar antes del merge**:

1. `Lint Python`, `Unit tests (Python)` y `Lint TypeScript` en verde.
2. Si toca `docker/`: `Build runtime templates / summary` en verde (incluye el
   Trivy de cada template).
3. Si toca dependencias Python: `uv lock --check` en verde. **Dependabot no
   regenera `uv.lock`** — si el PR cambia rangos de un `pyproject.toml`, hay que
   añadir un commit con `uv lock` + reexport de `constraints.txt` antes de
   mergear. Es el fallo más frecuente de estos PRs.
4. Leer el changelog del salto cuando sea `major` (los `major` llegan en PRs
   individuales a propósito; los `minor`/`patch` vienen agrupados).

**Qué NO hacer**: mergear en bloque sin mirar. Los PRs de dependencias son la
vía natural por la que entra una dependencia comprometida.

**Cadencia**: si los PRs de Dependabot se acumulan más de dos semanas, el
digest-pinning de este plan se vuelve contraproducente (congela CVEs en vez de
protegerlas). Vaciar la cola es parte del trabajo, no un extra.

---

## Verificación

```bash
# Las guardas estáticas de toda la cadena de suministro
.venv/Scripts/python.exe -m pytest tests/unit/test_supply_chain_config.py -q

# El lock no ha derivado
uv lock --check
```

## Relacionado

- [Referencia: cadena de suministro](../04-reference/cadena-suministro.md) — la
  ficha técnica de qué se escanea, con qué umbral y qué queda sin pinear.
- [ADR 0147 — lockfile Python: uv workspace](../05-architecture-decisions/0147-lockfile-python-uv-vs-pip-tools.md)
- [ADR 0148 — distribución de las imágenes runtime](../05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md) (`proposed`)
- [Plan prod-11 — cadena de suministro](../roadmap/prod-11-cadena-suministro.md)
- [Auditoría de dependencias 2026-06](./auditoria-dependencias-2026-06.md)
- [gotchas del toolchain](../03-guides/gotchas/)
