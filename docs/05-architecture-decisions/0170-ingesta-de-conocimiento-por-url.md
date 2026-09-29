---
adr_id: "0170"
title: "Ingesta de conocimiento por URL: si el asistente «Añadir conocimiento» acepta un enlace"
status: proposed
date: 2026-09-29
decided_on: null
authors: [claude-opus-5-5]
plan_referenced: ui-reestructuracion-2026-09-09
related: ["0094", "0142", "0155"]
docs_language: es
---

# ADR 0170 — Ingesta de conocimiento por URL

> **Estado: `proposed`.** Lo abre `task_ui_21` (plan `ui-reestructuracion-2026-09-09`),
> cuyo enunciado pide que el asistente «Añadir conocimiento» acepte «subir documentos o
> pegar texto/URL». La mitad «URL» no tiene dominio detrás, y el criterio de cierre 6 del
> plan prohíbe inventarla. Decide el operador.

## Contexto

La ingesta de una base de conocimiento sólo recibe **ficheros**:
`POST /knowledge-bases/{kb_id}/documents` guarda el binario, lo pasa por ClamAV y encola
el worker de ingesta, que lo convierte con docling-serve. No hay en el código ninguna vía
que parta de una URL: ni campo en `documents`, ni parámetro en el endpoint, ni paso en el
pipeline. Lo comprobó la revisión adversarial del 2026-09-29 (lente A, gap 2).

`task_ui_21` entregó las dos partes que sí tienen dominio: subir ficheros y **pegar
texto**, que viaja como un `.md` por el mismo endpoint de subida. La URL quedó fuera, y
hasta este ADR esa omisión sólo constaba en un comentario de
`components/knowledge/add-knowledge-wizard.tsx`.

Traer una URL no es sólo un campo más:

- **Es una petición saliente a un destino que elige el usuario.** Sin controles, es un
  SSRF de manual (CWE-918): `http://vault:8200/…`, `http://169.254.169.254/…` o cualquier
  servicio del compose que no debería verse desde fuera.
- **La plataforma ya tiene una política de salida**, pero para otras cosas: la allowlist
  de hosts MCP (`egress/mcp_allowlist.py`) y el registry-proxy de los runtimes (ADR 0094).
  Ninguna cubre al worker de ingesta.
- **Una página web no es un documento estable.** Hay que decidir si se guarda una
  instantánea (lo que se indexó) o se vuelve a traer, y qué pasa con el HTML dinámico.

## Opciones

### A — No hay ingesta por URL (lo entregado hoy)

El asistente ofrece ficheros y texto pegado. Quien quiera indexar una página la guarda
como PDF o HTML y la sube.

- **A favor:** cero superficie nueva de red y ningún trabajo pendiente.
- **En contra:** incumple una línea del enunciado de `task_ui_21`, que habría que retirar
  con este ADR (`rejects:` parcial, con la nota en negativo en la casilla).

### B — URL por el worker de ingesta, con allowlist y sin red interna

El endpoint acepta `source_url`. El worker descarga el contenido **una vez**, lo guarda
como documento normal (misma ruta de ClamAV, del sello de embedding del ADR 0155 y de la
cuota) y anota la URL como procedencia. La descarga pasa por un resolvedor que rechaza IP
privadas, de loopback y link-local **después** de resolver el DNS, sigue como mucho tres
redirecciones revalidando cada salto, y exige que el host esté en una allowlist de
plataforma (`platform_settings`, igual que la MCP). Tamaño y tiempo máximos iguales a los
de la subida.

- **A favor:** cumple el enunciado sin abrir la red interna, y lo indexado es una
  instantánea reproducible.
- **En contra:** es una feature con su test de seguridad (SSRF, rebinding de DNS,
  redirecciones) y un ajuste de plataforma nuevo, unos 2 días.

### C — URL delegada a docling-serve

docling-serve acepta fuentes HTTP por sí mismo: bastaría con pasarle la URL.

- **A favor:** poco código.
- **En contra:** la petición saliente la hace un contenedor que hoy no tiene política de
  salida, y la validación del destino queda fuera de nuestro código. Es la opción que el
  principio de aislamiento (CLAUDE.md, principio 2) pide no tomar.

## Recomendación

**A ahora, y B si el operador quiere la URL.** C se descarta por la razón escrita. Entre
A y B no hay una respuesta técnica mejor, porque la pregunta es de producto: si «pegar
una URL» vale dos días y un ajuste de plataforma más. Hasta que se decida, `task_ui_21`
queda **abierta en su mitad URL**, con esta referencia en su nota de cierre.

## Consecuencias

- **Si se elige A:** el ADR pasa a `accepted` con `rejects:` sobre la mitad URL de
  `task_ui_21`, y la casilla se cierra con la nota en negativo.
- **Si se elige B:** se abre una casilla nueva en el plan con el test de SSRF como
  requisito, y `task_ui_21` se cierra cuando pase.
