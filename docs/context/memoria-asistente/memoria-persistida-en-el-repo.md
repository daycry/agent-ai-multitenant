---
name: memoria-persistida-en-el-repo
description: La memoria vive espejada en docs/context/memoria-asistente/ del repo para sobrevivir a un cambio de ordenador — hay que mantenerla sincronizada
metadata:
  node_type: memory
  type: project
  originSessionId: 5d8f55fb-8d51-43ab-8655-49099d7db010
  modified: 2026-07-30T18:47:24.517Z
---

El 2026-07-30, por orden del operador («persístela en el repositorio, para poder
exportarla en otro ordenador sin perder conocimiento aprendido»), la memoria se
espejó dentro del repo:

- **`docs/context/memoria-del-asistente.md`** — el punto de entrada curado: órdenes
  permanentes del operador, constantes del proyecto que no se deducen del código, y
  la cola de pendientes que no vive en ningún plan. Está enlazado desde el §«Contexto
  Adicional» de CLAUDE.md, así que una sesión nueva lo encuentra.
- **`docs/context/memoria-asistente/`** — las 63 entradas **verbatim** con su
  `MEMORY.md`, como archivo para no perder detalle.

**Por qué importa:** este directorio (`~/.claude/projects/<slug>/memory/`) es local a
la máquina. Sin el espejo, cambiar de ordenador borraba meses de contexto.

**Cómo aplicarlo:**

1. Cuando esta memoria acumule algo que merezca sobrevivir, **vuelca otra vez** el
   directorio a `docs/context/memoria-asistente/` y actualiza el fichero curado. Sin
   ese paso, el espejo envejece y vuelve a mentir.
2. **No metas datos de ESTADO en el fichero curado** (cuántos commits, si un PR está
   abierto, cuántas casillas quedan). Eso caduca en horas: la propia entrada
   [[bloqueo-cierre-planes-pr-sin-mergear]] nació falsa el mismo día que se escribió.
   Estado se regenera con un comando; en el repo va solo lo durable.
3. El slug del directorio depende de la RUTA del proyecto y aquí es **minúscula**
   (`c--laragon-python-agent-ai-multitenant`). Cada subdirectorio tiene su propio
   slug y su propia memoria vacía.
