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

| Superficie                              | Herramienta                                                       | Dónde corre                                   | Umbral                             | Excepciones         |
| --------------------------------------- | ----------------------------------------------------------------- | --------------------------------------------- | ---------------------------------- | ------------------- |
| Dependencias Python (14 distribuciones) | `pip-audit --skip-editable` + `scripts/check_pip_audit_report.py` | `ci.yml` → job `security-scan`                | cualquier aviso conocido           | `.pip-audit-ignore` |
| npm `apps/admin-panel`                  | `npm audit --omit=dev`                                            | `ci.yml` → job `security-scan`                | `--audit-level=high`               | (ver §5)            |
| npm `apps/installer`                    | `npm audit --omit=dev`                                            | `ci.yml` → job `security-scan`                | `--audit-level=high`               | (ver §5)            |
| 5 imágenes de plataforma                | Trivy                                                             | `ci.yml` → job `build-images`                 | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| 14 runtime templates                    | Trivy                                                             | `build-runtime-templates.yml` (matriz)        | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| 5 imágenes publicables                  | Trivy                                                             | `release-images.yml`                          | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| Drift del lockfile                      | `uv lock --check`                                                 | `ci.yml` → job `lint-python` (**bloqueante**) | cualquier desincronía              | ninguna             |

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

**Estado a 2026-08-19: el gate está ENCENDIDO en el workflow.** `security-scan`
declara `continue-on-error: false` y un hallazgo SCA rompe el job. Queda **un
solo paso**, y es de un humano con permisos de administración del repo: añadirlo
a los _required status checks_ de la protección de rama (paso 1 de la checklist).

### Por qué se pudo encender (medición del 2026-08-19)

El job nació en modo informe a propósito: convertirlo en gate el día 1 habría
bloqueado todos los PRs con el backlog heredado de CVEs. Ese backlog **está
vacío**, medido en las tres superficies del job:

| Superficie                                              | Resultado                           |
| ------------------------------------------------------- | ----------------------------------- |
| `pip-audit -r constraints.txt`                          | _No known vulnerabilities_ (exit 0) |
| `npm audit --omit=dev --audit-level=high` (admin-panel) | _found 0 vulnerabilities_ (exit 0)  |
| `npm audit --omit=dev --audit-level=high` (installer)   | _found 0 vulnerabilities_ (exit 0)  |

Y sin apoyarse en ninguna supresión: `.trivyignore` y `.pip-audit-ignore` siguen
**sin una sola entrada vigente**.

> El pip-audit se corrió **sobre `constraints.txt`**, no sobre el `.venv` local, y esa distinción importa: el venv del repo se resolvió desde los rangos antes de que existiera el lock y va 74 paquetes por detrás (§2 de [cadena-suministro.md](../04-reference/cadena-suministro.md)). Auditarlo habría medido un árbol que CI no instala.

**Qué cambió respecto a las cuatro mediciones anteriores** (2026-07-31, 08-01,
08-10), todas con el mismo veredicto «bloqueado por `next`»: las dos superficies
npm ya no van por la línea 14. Van por **`next 15.5.23`**. El bloqueante que se
midió cuatro veces —el rango vulnerable `next 9.3.4-canary.0 – 16.3.0-preview.10`,
que ninguna 14.x ni 15.x anterior cerraba— **ya no aparece**. No hizo falta la
migración a next 16 que se daba por inevitable, ni la excepción razonada que era
la alternativa: el ecosistema publicó el parche y alguien lo aplicó.

> **La lección, porque va a repetirse:** durante tres semanas la conclusión
> escrita fue «esto sólo se cierra con un plan de migración a un major». Los
> avisos de npm cambian aunque el código no, **y las versiones publicadas
> también**. Antes de presupuestar un salto de major por un aviso de SCA, vuelve
> a medir.

### Si mañana aparece un `high` sin fix

No hay ignore-list de npm, **y es deliberado**: crear una vacía hoy sería un
mecanismo sin un solo llamante. Cuando haga falta se construye entonces, con el
aviso real como su primera entrada justificada. Lo que **no** es una salida:
bajar `--audit-level` a `critical`. Eso no documenta la excepción, la esconde, y
se lleva por delante todos los `high` futuros a la vez.

### Digests de las imágenes auxiliares (revisión manual)

`postgres:16-alpine` y `redis:7-alpine` —los sidecars que el worker levanta junto
al test-runtime— van fijados por digest en
`apps/workers/src/workers/test_runtime.py` (`DEFAULT_POSTGRES` / `DEFAULT_REDIS`).
Su vehículo de refresco **no es Dependabot**: el ecosistema `docker` parsea
Dockerfiles y ficheros compose, no fuentes Python. Por eso llevan
`# review: YYYY-MM-DD` como las excepciones de §5 y entran en el mismo calendario:

```bash
# Resolver el digest actual del tag (índice multi-arch, NO el de una plataforma)
docker buildx imagetools inspect postgres:16-alpine --format '{{.Manifest.Digest}}'
docker buildx imagetools inspect redis:7-alpine     --format '{{.Manifest.Digest}}'
```

Lo mismo para los cinco tipos del catálogo del ADR 0129
(`workers/runtime_services.py::SERVICE_CATALOG`: mysql, mariadb, postgres, redis,
beanstalkd), que comparten fecha de revisión con los dos de arriba.

Si el digest ha cambiado, actualizar la constante **y su comentario de versión**
(`# postgres:16-alpine == 16.15-alpine3.24`), y mover la fecha de `review:`.
Guardado por `tests/unit/test_aux_images_pinned_by_digest.py`, que además exige
que el lanzamiento honre el digest y aborte si no lo puede resolver (ADR 0148).

### Checklist del operador (2026-08-19)

Queda esto, en este orden:

- [ ] **1. Arreglar la facturación de la cuenta ANTES de tocar branch
      protection.** CI lleva caído desde el 2026-07-30 por «recent account
      payments have failed» (<https://github.com/settings/billing>). Un check
      requerido que nunca corre **bloquea todos los merges**: hacer el paso 2 con
      CI caído deja el repo sin poder integrar nada. Este orden no es
      cosmético.
- [ ] **2. Añadir el check a branch protection.** GitHub → **Settings → Branches
      → `master` → Require status checks to pass** → añadir el check con su
      nombre exacto: **`SCA (pip-audit + npm audit)`**. Es el `name:` del job, no
      el id `security-scan`; con el id el check no casa nunca y la protección
      queda de adorno sin avisar.
- [ ] **3. Verificar** con el test humano `human_prod11_01`: rama que degrade una
      dependencia a una versión vulnerable conocida → el PR sale en rojo y branch
      protection impide mergearlo.

**Lo que ya no hay que hacer** (estaba en la checklist del 2026-08-02 y está
hecho): elegir entre migrar a next 16 o declarar una excepción —el backlog se
vació solo—, y quitar el `continue-on-error` —ya está en `false` explícito, con
`test_security_scan_is_an_enforcing_gate` impidiendo que se deshaga en silencio—.

**Lo que ya estaba listo**: las dos ignore-lists con justificación y fecha por
entrada (`test_sca_ignore_lists_exist_and_document_every_exception`), los tres
workflows escaneando sus imágenes con Trivy (24 imágenes, 9 pasos) y el lock sin
derivar (`uv lock --check` → exit 0).

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
