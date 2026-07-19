---
title: "ADR 0123: Bandeja unificada del humano"
status: accepted
date: 2026-07-19
---

# ADR 0123: Bandeja unificada del humano

Aprobada por el operador el 2026-07-19 (2ª tanda, «implementa todo»).

## Contexto

El cuello de botella nº1 del flujo agéntico es la decisión humana, y lo que
espera al humano vive repartido en cuatro pantallas: planes en
`pending_human_validation`, `approval_requests` pendientes (acciones
sensibles), runs `needs_human_review` y runs `awaiting_human_approval`.
Resultado observado: planes esperando validación durante días sin que nadie
lo viera de un vistazo.

## Decisión

`GET /human-queue` (cualquier miembro; RLS + predicado tenant_id): agrega
las cuatro fuentes en un shape uniforme (`kind`, título, proyecto,
`age_seconds`, `url_path`) ordenado por antigüedad — lo más viejo primero.
Página `/admin/human-queue` («Esperan tu decisión») con edad legible,
resaltado en rojo a partir de 24 h y clic que lleva a la pantalla REAL donde
se resuelve cada cosa. Solo lectura: la bandeja es una lente, nunca un
bypass de los gates (approve del plan, resolución del approval, ficha del
run conservan su RBAC y su doble firma).

## Consecuencias

- El humano ve su cola completa en una pantalla; la antigüedad visible
  convierte los olvidos en evidentes.
- Cero superficie de mutación nueva: sin riesgo de saltarse gates.
- La Oficina (ADR 0118) muestra QUIÉN espera; la bandeja es DÓNDE se
  resuelve — complementarias.
