---
name: tanda-hallazgos-prod12-2026-07-08
description: Tanda autónoma 2026-07-08 en plan/runs-visor-trabajo — hallazgos
metadata:
  node_type: memory
  type: project
  originSessionId: 46819ab5-f853-4ca2-aea8-a56ed20f06f1
---

Tanda «adelante con la cola» (2026-07-08, rama `plan/runs-visor-trabajo`, TDD + commit atómico):

**Hecho y comiteado**: reconciliación de roadmap (85be95b); H1 publish tras run-lock (d1d2f41);
H2 `transition_from_blocked` en todas las vías (50f4e5d); H3 botón Desbloquear en detalle+board
(c6e8d99); H4 app-preview sin placeholder + sección de ajustes (e983ada); H5 pcov en php-phpunit
y php-pest, imágenes ya reconstruidas (b2dd85f); T4 guard AST de mutación de estado con edge
`pending_human_validation→blocked` declarado (f123489); tests frontend jsdom+testing-library
NUEVA infra (jsx automatic en vitest.config, entorno por fichero) con B2/B3/C1/C2/D1/T11+i18n
(e1ff76c, vitest 189); H6 contrato de claves AgentState ejecutable (e661201); prod-12 Fases A+B
completas — ssrf_guard con pinning/Host/SNI/no-redirects + columna `projects.allowed_domains`
(migración **0105**, head nuevo) + cableado con centinela (9cd2eb5); docker_command falla-rápido
→ stack_exec, opción b (4d53f92). `runs-visor-trabajo` y `ciclo-vida-planes-fixes` → **completed**
(0b923e2); `refactor-pipeline-ejecucion-review` → completed en la reconciliación.

**Why:** el operador pidió ejecutar la cola completa de pendientes; el checkbox = realidad
verificada (cada [x] con test verde ese día).

**Sub-tanda 2 (mismo día, tras «continua»)**: reaper_01 HECHO (0c93ed3 — `workers.reap_orphans`
cada 10 min, criterio de vida compartido con el sweeper de zombis, redes test-runtime vacías) y
av_01 HECHO (dbfae87 — **ADR 0105** fail-closed, estado `pending_scan` con migración **0106**,
sweep re-encola, notificación `antivirus_unreachable` 15 min/re-aviso 6 h). Segundo deploy con
0106 aplicado.

**Sub-tanda 3 (mismo día)**: img_01 HECHO (e5e929a — 14 templates USER 1000 + HOME=/home/agent,
catálogo dep-cache repuntado, PHP rebuilds verificados) y cadv_01 HECHO (0fea7b2 — cAdvisor SIN
privileged validado empíricamente, cuarentenas sandbox-8 retiradas, 0 privileged en pentest;
recreado en dev: privileged=false capdrop=ALL, métricas OK). HALLAZGO PREEXISTENTE destapado:
`cortex_conversations` sin RLS (migración 0092) — test de pentest en rojo, revisar aparte.

**INCIDENTE del mismo día (resuelto en 2 vueltas)**: (1) mis rebuilds del admin-panel omitieron
`--build-arg NEXT_PUBLIC_API_URL=/api` → bundle con fallback `localhost:8001`; (2) al añadir el
build-arg DESDE GIT BASH, MSYS lo mangleó a `C:/Program Files/Git/api` → el navegador hacía
`fetch(file:///C:/Program Files/Git/api/...)`. Ambos = «Could not reach the server» + OAuth sin
cargar. LATENTE mientras hubo sesiones en Redis; afloró cuando un reinicio limpio del motor
Docker (~15:56Z, no causado por mí) las vació. Fix verificado con sonda Chromium headless
(sso/providers 200 + login 401 correcto). REGLA: el build del panel SIEMPRE desde PowerShell,
contexto `apps/admin-panel` + build-arg `/api`; verificación funcional con la sonda. Gotcha
`admin-panel-build-context-is-app-dir.md` (55ce83d).

**How to apply:** lo genuinamente pendiente: prod-12 — net*01 mitad marketplace, docs_01
(+ UI de allowed_domains + retirar run*\* de seeds), mkt_01 BLOQUEADA por materialización de
artefactos [[auditoria-memoria-tools-marketplace]]; hallazgo #7 (decisión de producto), #8 e2e
Docker-real (= prod-17), #9 refactor frontend. Head de migraciones = **0106**. OJO: el resto de
templates de test-runtime (node/go/java/...) aún no están construidos localmente — se hornean
no-root al primer build. Los render-tests del panel usan `// @vitest-environment jsdom` por
fichero; los mounts necesitan `LanguageProvider`.
Ver [[refactorizacion-por-partes]] y [[implementacion-auditoria-2026-07-04]].
