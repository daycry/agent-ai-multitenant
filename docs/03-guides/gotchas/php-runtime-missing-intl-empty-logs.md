---
title: "php spark / CI4 sale 255 con logs VACÍOS (falta ext-intl; production oculta el fatal)"
area: docker
encountered: 2026-06-30
stack: docker · php-phpunit · CodeIgniter 4 · stack_exec · ADR 0093
---

## Síntoma

`stack_exec("php spark …")` (o cualquier comando que arranque CodeIgniter 4) en el
runtime `php-phpunit` devuelve **exit 255 con `logs` VACÍOS** y `timed_out=false`:

```json
{ "exit_code": 255, "logs": "", "timed_out": false }
```

`php -v` y `php -r` funcionan (rc 0); solo falla al bootstrapear CI4.

## Causa raíz

**Dos cosas combinadas:**

1. **Falta `ext-intl`.** CodeIgniter 4 usa la clase `Locale` (de la extensión `intl`) en
   su `I18n/Time`. La imagen `php:8.3-cli` NO trae `intl` por defecto, así que el bootstrap
   de CI4 fatal-ea con `Uncaught Error: Class "Locale" not found` (PHP CLI → exit **255**).
2. **`display_errors=0` lo oculta.** Sin `.env`, CI4 asume `ENVIRONMENT=production`, que
   pone `display_errors=0`. Por eso el fatal **no imprime nada** → los `logs` salen vacíos.
   El 255 + vacío parece un misterio hasta que fuerzas el error.

## Cómo destapar el error real

```bash
# Forzar entorno dev + errores visibles revela el fatal:
CI_ENVIRONMENT=development php -d display_errors=stderr -d error_reporting=-1 spark list 2>&1
# -> Fatal error: Uncaught Error: Class "Locale" not found in .../I18n/TimeTrait.php
```

Regla general: **un comando del runtime con rc≠0 y `logs` vacío suele ser un fatal de PHP
con `display_errors=0`** (production). Re-córrelo con `-d display_errors=stderr` para verlo.

## Fix

Añadir `intl` (y `soap`, para tests/clientes SOAP) a los Dockerfiles de los runtimes PHP
(`docker/agent-runtimes/php-phpunit/Dockerfile` + `php-pest`):

```dockerfile
RUN apt-get install -y --no-install-recommends ... libicu-dev libxml2-dev \
 && docker-php-ext-install -j$(nproc) ... mbstring intl soap
```

`intl` necesita `libicu-dev`; `soap` necesita `libxml2-dev`. Reconstruir las imágenes:

```bash
docker build -t agent-runtime-php-phpunit:v1 docker/agent-runtimes/php-phpunit/
docker build -t agent-runtime-php-pest:v1    docker/agent-runtimes/php-pest/
```

## Cómo verificar

```bash
docker run --rm --entrypoint php agent-runtime-php-phpunit:v1 -m | grep -iE 'intl|soap|mbstring'
# -> intl / mbstring / soap
# y sobre un worktree CI4 con vendor/ instalado: `php spark list` -> rc 0 (lista comandos).
```

## Relacionado

- `ADR 0093` (stack_exec) — el camino por el que el agente corre `php spark`.
- Si otro stack falla con rc≠0 + logs vacíos, sospecha de la misma trampa (extensión PHP
  ausente + production ocultando el fatal).
