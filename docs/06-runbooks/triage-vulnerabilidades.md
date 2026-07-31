---
title: Triage de vulnerabilidades y política de excepciones
docs_language: es
audience: operador, tech lead, desarrollador
updated: 2026-07-31
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

| Superficie                              | Herramienta                          | Dónde corre                                   | Umbral                             | Excepciones         |
| --------------------------------------- | ------------------------------------ | --------------------------------------------- | ---------------------------------- | ------------------- |
| Dependencias Python (13 distribuciones) | `pip-audit --strict --skip-editable` | `ci.yml` → job `security-scan`                | cualquier aviso conocido           | `.pip-audit-ignore` |
| npm `apps/admin-panel`                  | `npm audit --omit=dev`               | `ci.yml` → job `security-scan`                | `--audit-level=high`               | (ver §5)            |
| npm `apps/installer`                    | `npm audit --omit=dev`               | `ci.yml` → job `security-scan`                | `--audit-level=high`               | (ver §5)            |
| Imagen base `api-server`                | Trivy                                | `ci.yml` → job `build-images`                 | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
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
2. **Quitar el `continue-on-error`** del job en `.github/workflows/ci.yml` y
   añadir `SCA (pip-audit + npm audit)` a los **checks requeridos de branch
   protection** (Settings → Branches → `master`). Esa lista la administra
   [`prod-02-ci-en-verde`](../roadmap/prod-02-ci-en-verde.md); este plan la
   extiende, no la redefine. Requiere permisos de administración del repo.

Verificación de que el gate hace lo que dice (test humano `human_prod11_01`):
crear una rama que degrade una dependencia a una versión vulnerable conocida y
comprobar que el PR sale en rojo y que branch protection impide mergearlo.

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

- [ADR 0147 — lockfile Python: uv workspace](../05-architecture-decisions/0147-lockfile-python-uv-vs-pip-tools.md)
- [Plan prod-11 — cadena de suministro](../roadmap/prod-11-cadena-suministro.md)
- [Auditoría de dependencias 2026-06](./auditoria-dependencias-2026-06.md)
- [gotchas del toolchain](../03-guides/gotchas/)
