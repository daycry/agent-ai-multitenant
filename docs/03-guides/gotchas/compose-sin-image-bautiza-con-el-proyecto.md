---
title: Un servicio con `build:` y sin `image:` acaba con un nombre que depende del proyecto
area: docker
encountered: 2026-08-22
stack: docker compose v2.x, GitHub Actions, Trivy 0.36
---

## Síntoma

En la máquina del operador convivían dos imágenes para el **mismo** Dockerfile:

```console
$ docker images | grep egress
agentic-platform-egress-proxy   latest   ...   hace 2 meses
agentic-egress-proxy            v1       ...   hace 3 días
```

Y ninguna de las dos era la que se creía. La pregunta que no se podía responder
—«¿la imagen que está corriendo es la que construyó CI?»— no tenía respuesta
posible, porque las dos cosas se llamaban distinto por construcción.

## Causa raíz

Un servicio declarado con `build:` y **sin** `image:` no deja el nombre sin
poner: compose lo pone por ti, y lo forma con el nombre del proyecto —
`<COMPOSE_PROJECT_NAME>-<servicio>`. Es decir, el nombre de la imagen depende de
**dónde** se levante el stack, no de lo que se construye.

Con eso, tres actores que construían el mismo `docker/egress-proxy/Dockerfile`
producían tres nombres:

| Actor                           | Nombre resultante                            |
| ------------------------------- | -------------------------------------------- |
| `docker/docker-compose.yml`     | `agentic-platform-egress-proxy:latest`       |
| `.github/workflows/ci.yml`      | `agentic-egress-proxy:v1`                    |
| El compose que genera el wizard | `<proyecto-de-esa-instalación>-egress-proxy` |

Y de ahí salen dos daños encadenados, ninguno de los cuales da un error:

1. **El escaneo se pierde.** CI construía su `agentic-egress-proxy:v1` y lo
   tiraba: no lo publicaba (no está en `release-images.yml`) ni lo escaneaba
   (Trivy iba por una lista de `image-ref` escritos a mano). Así que el
   `egress-proxy` —la ÚNICA salida a internet del contenedor donde corre código
   no confiable, ADR 0019— nunca pasó por Trivy.
2. **El despliegue no se nota.** Como el nombre que corre nunca coincidió con el
   que CI construye, nadie podía ver que el que corría llevaba dos meses sin
   reconstruirse, con la allowlist de entonces.

## Fix

Declarar el `image:` **además** del `build:`, con el mismo nombre completo en los
tres sitios (`agentic-platform/<servicio>:v1`):

```yaml
egress-proxy:
  build: ./egress-proxy
  image: ${IMAGE_EGRESS_PROXY:-agentic-platform/egress-proxy:v1}
```

Con `build:` presente, compose sigue construyendo la imagen cuando falta; lo
único que cambia es que ahora se llama igual la construya quien la construya. En
`ci.yml`, el bucle etiqueta con ese mismo nombre y hay un paso de Trivy por cada
una.

Ojo con el tag: coincidir sólo en el prefijo **no** basta. `…/egress-proxy:v1` y
`…/egress-proxy:v2` son dos imágenes distintas y devuelven el problema entero.

## Cómo verificar el fix

```console
$ python -m pytest tests/unit/test_infra_images_are_scanned.py -q
12 passed, 2 skipped
```

La guarda deriva la lista de `docker/*/Dockerfile` en vez de enumerarla —
enumerar a mano fue el modo de fallo original del `watchdog`, ver
`tests/unit/test_app_images_are_built_by_ci.py`— y compara el nombre **completo**
que construye `ci.yml` con el que levanta cada compose. Si divergen, dice cuál
levanta cada uno.
