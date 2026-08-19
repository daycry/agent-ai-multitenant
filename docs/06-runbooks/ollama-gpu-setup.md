---
title: Ollama en el stack — CPU/GPU y embeddings locales
docs_language: es
audience: operador, system admin
updated: 2026-06-09
---

# Runbook — Ollama en el stack (CPU/GPU) y embeddings locales

La plataforma usa **Ollama** para los **embeddings** de KBs y memoria (y,
opcionalmente, para servir LLMs locales). Desde el **ADR 0056** Ollama es un
**servicio del stack** con tres modos: **`none` / `cpu` / `gpu`**. Este runbook
cubre cómo levantarlo en dev y en producción (instalador), cómo activar la GPU
(NVIDIA/CUDA, incl. Windows/WSL2) y cómo diagnosticar los fallos típicos de
embeddings.

> **Tope de diseño (ADR 0056 + ADR 0155):** la columna pgvector es de **768
> dims** y la plataforma indexa con **un único modelo**, el de
> `API_SERVER_EMBEDDING_MODEL`. Por eso el embedder debe ser de **768 dims**
> (`nomic-embed-text` lo es) y **no se cambia en caliente**: cada KB lleva el
> sello del modelo con el que se generaron sus vectores, y en cuanto deja de
> coincidir con el activo sale del camino vectorial y rechaza documentos nuevos
> hasta que se reindexe. El procedimiento está en el
> [ADR 0155](../05-architecture-decisions/0155-modelo-de-embeddings-de-kb.md);
> el naming, en el [gotcha](../03-guides/gotchas/ollama-embedding-model-naming.md).

## Modos

| Modo   | Qué hace                                                                | Cuándo                                                 |
| ------ | ----------------------------------------------------------------------- | ------------------------------------------------------ |
| `none` | No se levanta Ollama. Embeddings vía Ollama externo/cloud, o solo BM25. | Ya tienes un Ollama gestionado, o no quieres vectores. |
| `cpu`  | Servicio `ollama` sin GPU. Suficiente para embeddings y LLMs pequeños.  | **Default recomendado.**                               |
| `gpu`  | Como `cpu` + reserva NVIDIA (CUDA) para LLMs locales acelerados.        | Hay GPU NVIDIA + NVIDIA Container Toolkit.             |

En `cpu`/`gpu` un init one-shot **`ollama-bootstrap`** hace `ollama pull` del
modelo de embeddings cuando `ollama` está _healthy_ (idempotente; el modelo
persiste en el volumen). Sin él, el primer `/api/embed` daría `model not found`.

## Dev (Docker Compose)

```bash
# CPU (base): el servicio ollama + bootstrap ya están en docker-compose.yml.
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d

# GPU: añade el overlay con la reserva NVIDIA.
docker compose -f docker/docker-compose.yml \
               -f docker/docker-compose.dev.yml \
               -f docker/docker-compose.gpu.yml up -d
```

En dev la api-server suele correr **fuera** de Docker; el override de dev expone
`ollama` en `localhost:11434`, que es justo el default de `API_SERVER_OLLAMA_URL`.
El modelo a descargar se controla con `EMBEDDING_MODEL` (default `nomic-embed-text`,
ver `docker/.env.example`).

Verifica:

```bash
docker compose -f docker/docker-compose.yml logs ollama-bootstrap   # debe terminar OK
curl http://localhost:11434/api/tags                                # lista modelos
```

## Producción (instalador)

En el paso **Recursos** del wizard elige el modo (Ninguno / CPU / GPU) y, si
procede, el modelo de embeddings. El instalador:

- añade los servicios `ollama` + `ollama-bootstrap` cuando el modo ≠ `none`;
- pone la **reserva NVIDIA solo** en `gpu`;
- cablea el embedder: inyecta `API_SERVER_OLLAMA_URL=http://ollama:11434`,
  `API_SERVER_EMBEDDING_MODEL` y `WORKERS_MEMORY_EMBEDDER_BASE_URL`.

(Compat: una config antigua con `gpu_enabled: true` se mapea a `ollama_mode: gpu`.)

## Activar la GPU (NVIDIA/CUDA)

La reserva `deploy.resources.reservations.devices` requiere el **NVIDIA Container
Toolkit** en el host.

### Linux

```bash
# Driver NVIDIA instalado (nvidia-smi responde) + toolkit:
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
# (añade el repo correspondiente a tu distro) ...
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Windows (Docker Desktop + WSL2)

1. **Driver NVIDIA para WSL** en Windows (el del fabricante, con soporte WSL).
2. **Docker Desktop** con backend **WSL2** activado.
3. Dentro de la distro WSL2, instala el **NVIDIA Container Toolkit** (pasos Linux).
4. Reinicia Docker Desktop.

> Sin GPU/Toolkit, usa **`cpu`** (o un Ollama externo/cloud con `none`). No es un
> requisito: la GPU solo acelera LLMs locales; los embeddings van bien en CPU.

### Verificación de GPU

```bash
docker exec agentic-platform-ollama-1 nvidia-smi      # la GPU se ve dentro del contenedor
docker exec agentic-platform-ollama-1 ollama ps       # PROCESSOR debería decir GPU al servir
```

## Gestión de modelos (admin nativo)

El System Admin gestiona Ollama desde **Admin → Plataforma → «Ollama &
Embeddings»** (no hay Open WebUI en el stack — ver ADR 0056 U-B):

- **Embeddings**: modelo activo, accesibilidad de Ollama, embedders instalados
  con su compatibilidad 768, y recomendados.
- **Modelos Ollama**: listar / **pull** / borrar (endpoints `/admin/ollama/*`).

## Troubleshooting

| Síntoma                                                       | Causa probable                                   | Acción                                                                                                                                                 |
| ------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ollama embed request failed: All connection attempts failed` | api-server no llega a Ollama                     | Comprueba `API_SERVER_OLLAMA_URL` (en el stack `http://ollama:11434`; en dev host `http://localhost:11434`) y que el servicio `ollama` esté _healthy_. |
| `model not found` al embeber                                  | modelo no descargado o nombre con sufijo `-v1.5` | Usa el nombre real (`nomic-embed-text`); revisa que `ollama-bootstrap` terminó OK.                                                                     |
| KBs indexadas pero sin vectores (solo BM25)                   | embeddings caídos (no fatal)                     | Igual que arriba; re-indexa tras arreglar el endpoint.                                                                                                 |
| `ollama returned a N-dim vector, expected 768`                | modelo de dims ≠ 768                             | Elige un embedder de 768 (`nomic-embed-text`); cambiar de dims con KBs existentes es Plan 12.                                                          |
| GPU no usada (`ollama ps` dice CPU)                           | falta NVIDIA Container Toolkit o el overlay GPU  | Instala el toolkit y levanta con `docker-compose.gpu.yml` / modo `gpu`.                                                                                |

## Apéndice — Open WebUI como playground (opcional, fuera del stack)

Open WebUI **no** forma parte del stack (sería una puerta paralela sin
tenancy/guardrails — ADR 0056 U-B). Un dev que quiera un playground puede
levantarlo por su cuenta apuntando al Ollama expuesto:

```bash
docker run -d --name open-webui -p 3000:8080 \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  ghcr.io/open-webui/open-webui:main
```

No lo uses como vía de acceso de la plataforma a los LLMs: la gobernanza
(tenant, guardrails, budgets) vive en la app, no en Open WebUI.
