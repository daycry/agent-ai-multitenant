---
adr: "0003"
title: HashiCorp Vault desde el día uno (en vez de `.env`)
status: accepted
date: 2026-05-20
deciders: System Architect
phase: 00-fundaciones
---

# ADR 0003 — HashiCorp Vault desde el día uno (en vez de `.env`)

## Contexto

Una plataforma multi-tenant maneja secretos sensibles desde la
primera fila: credenciales de PostgreSQL, claves de firma JWT,
tokens de API de proveedores LLM (Anthropic, OpenAI, Gemini),
credenciales de OAuth (Copilot Device Flow), claves de cifrado de
backups, certificados...

Las opciones más comunes para guardarlos:

- **`.env` files commiteados** — inaceptable, fuga garantizada.
- **`.env` files ignorados por git** — frágil: alguien los copia
  por mail, se filtran en logs, se quedan en backups.
- **Variables de entorno del contenedor pasadas por compose** —
  mejor, pero (a) el operador acaba escribiéndolas en un `.env`,
  (b) `docker inspect` las expone, (c) no rotables sin reiniciar.
- **Secret manager dedicado** — Vault, AWS Secrets Manager, GCP
  Secret Manager, etc.

## Decisión

Incluir **HashiCorp Vault** como **uno de los cinco servicios de
infraestructura base** del stack (`postgres`, `redis`, `minio`,
**`vault`**, `clamav`). Desplegarlo desde la Fase 00, no esperar a
Fase 15 (instalador).

- **Producción:** Vault en `server` mode con storage `file` (volumen
  persistente), Shamir 5-of-3 sealing, KV v2 en `secret/`.
- **Desarrollo:** Vault en `-dev` mode con root token conocido
  (`dev-root-token`). In-memory, sin persistencia. Reset trivial.
- Las credenciales generadas al primer arranque (passwords de
  Postgres, claves JWT) viven en Vault desde el minuto uno.
- `.env.example` documenta valores **dev-only** que el operador
  reemplaza vía Vault al instalar (Fase 15).

## Alternativas descartadas

1. **Esperar a Fase 15.** Rechazado: deuda técnica costosa.
   Empezamos con auth + multi-tenancy, los servicios necesitan
   credenciales reales para funcionar bien. Si las almacenamos en
   `.env` durante 14 fases, migrar después implica refactorizar
   muchos puntos.
2. **AWS Secrets Manager / GCP Secret Manager.** Rechazado para
   Fase 00: el sistema es **on-prem mono-máquina**, no asume cloud.
   Vault es agnóstico del proveedor.
3. **`sops` + `age`.** Funciona para secretos en disco, pero no
   resuelve **rotación**, **revocación**, **audit log**, ni
   **dynamic secrets** (credenciales de DB efímeras).

## Consecuencias

Positivas:

- Rotación de secretos sin redeploy.
- Audit log centralizado de quién leyó qué secreto.
- En Fase 15, el instalador genera todas las credenciales
  automáticamente y las almacena en Vault — el operador nunca las
  ve en texto plano.

Negativas / cuidados:

- Operacionalmente: Vault tiene que estar **unsealed** para
  funcionar. Pérdida de las 3+ unseal keys → datos perdidos. El
  procedimiento de backup de las keys es un **runbook obligatorio**
  (`docs/06-runbooks/`).
- Dev y prod usan modos distintos. El gotcha "config.hcl + -dev →
  EADDRINUSE" lo aprendimos por las malas
  ([nota](../03-guides/gotchas/vault-dev-mode-port-conflict.md)).
- Una dependencia más en el stack. Mitigado por el watchdog que la
  reinicia si cae.

## Referencias

- `docker/vault/config.hcl` — config de producción.
- `scripts/init-vault.sh` — bootstrap idempotente (Shamir 5-of-3).
- Documento maestro, sección 17.4.
