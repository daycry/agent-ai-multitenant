"""Sidecar WhatsApp neonize/whatsmeow — implementación de referencia (ADR 0109).

Expone el contrato HTTP mínimo que consume el adapter del notification-dispatcher
(`channels/whatsapp.py`, provider "neonize"):

    POST /send   {"to": "<msisdn o JID>", "text": "..."}  (Bearer NEONIZE_TOKEN)
    GET  /health → {"paired": bool}

Estado EXPERIMENTAL: el emparejamiento real (QR) solo puede validarlo el
operador con un número — ver docs/06-runbooks/whatsapp-neonize-pairing.md.
La sesión whatsmeow persiste en /data (volumen); al arrancar sin sesión, el QR
se imprime en los logs del contenedor (`docker logs -f whatsapp-neonize`).

whatsmeow habla el protocolo WhatsApp Web NO oficial: Meta puede banear el
número. Uso recomendado: avisos operativos internos (ADR 0109).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("whatsapp-neonize")

_TOKEN = os.environ.get("NEONIZE_TOKEN", "")
_SESSION_DB = os.environ.get("NEONIZE_SESSION_DB", "/data/session.sqlite3")

app = FastAPI(title="whatsapp-neonize sidecar", docs_url=None, redoc_url=None)

_state: dict[str, Any] = {"client": None, "paired": False}


class SendRequest(BaseModel):
    to: str
    text: str


def _require_token(request: Request) -> None:
    if not _TOKEN:
        raise HTTPException(status_code=503, detail="NEONIZE_TOKEN not configured")
    if request.headers.get("authorization") != f"Bearer {_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid bearer token")


@app.on_event("startup")
async def _connect() -> None:
    """Conecta el cliente neonize en background; sin sesión, imprime el QR en
    los logs para el emparejamiento inicial (runbook). Import diferido: si
    neonize no está instalado el sidecar arranca degradado (503 en /send)."""
    try:
        from neonize.aioze.client import NewAClient
        from neonize.events import ConnectedEv, PairStatusEv
    except Exception as exc:  # pragma: no cover - dependencia opcional
        _log.error("neonize no disponible: %s — /send responderá 503", exc)
        return

    client = NewAClient(_SESSION_DB)

    @client.event(ConnectedEv)
    def _on_connected(_client: Any, _event: Any) -> None:
        _state["paired"] = True
        _log.info("sesión WhatsApp conectada")

    @client.event(PairStatusEv)
    def _on_pair(_client: Any, event: Any) -> None:
        _state["paired"] = True
        _log.info("emparejado: %s", event)

    _state["client"] = client

    async def _run() -> None:
        try:
            # Sin sesión previa, neonize imprime el QR en stdout (logs del
            # contenedor) hasta que el operador lo escanee.
            await client.connect()
        except Exception as exc:  # pragma: no cover - conexión es best-effort
            _log.error("conexión neonize falló: %s", exc)

    asyncio.get_running_loop().create_task(_run())


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"paired": bool(_state["paired"]), "client": _state["client"] is not None}


@app.post("/send")
async def send(body: SendRequest, request: Request) -> dict[str, Any]:
    _require_token(request)
    client = _state["client"]
    if client is None:
        raise HTTPException(status_code=503, detail="neonize client unavailable")
    if not _state["paired"]:
        raise HTTPException(status_code=409, detail="not_paired")
    try:
        from neonize.utils import build_jid

        jid = build_jid(body.to.lstrip("+").replace(" ", ""))
        result = await client.send_message(jid, body.text)
        message_id = getattr(result, "ID", None) or getattr(result, "id", None)
        return {"ok": True, "id": str(message_id) if message_id else None}
    except Exception as exc:
        _log.error("send falló: %s", exc)
        raise HTTPException(status_code=502, detail=f"send failed: {type(exc).__name__}") from exc
