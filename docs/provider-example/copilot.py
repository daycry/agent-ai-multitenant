"""GitHub Copilot vía OAuth device flow.

AVISO: Copilot no expone una API pública oficial para terceros.
Los endpoints usados aquí son los que utiliza el plugin oficial de VS Code.
Pueden cambiar sin previo aviso y su uso fuera del IDE puede infringir los
Términos de Servicio de GitHub. Úsalo bajo tu propia responsabilidad.

Flujo:
  1. POST https://github.com/login/device/code  -> device_code + user_code
  2. El usuario abre verification_uri y mete user_code
  3. Poll a https://github.com/login/oauth/access_token -> github_token (no expira)
  4. GET https://api.github.com/copilot_internal/v2/token con github_token
     -> copilot_token (expira ~30 min, hay que refrescar)
  5. POST https://api.githubcopilot.com/chat/completions con copilot_token
     (formato compatible OpenAI)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ..exceptions import AuthError, ProviderError, RateLimitError
from ..types import CompletionResponse, Message, StreamChunk, Usage

# Editor "oficial" que GitHub espera ver. El plugin de VS Code usa estos headers.
# Cambiar el client_id implica registrar tu propia GitHub App con scope de Copilot,
# cosa que GitHub no concede a apps de terceros normalmente.
VSCODE_CLIENT_ID = "01ab8ac9400c4e429b23"  # client_id público del plugin VS Code

EDITOR_HEADERS = {
    "Editor-Version": "vscode/1.95.0",
    "Editor-Plugin-Version": "copilot-chat/0.22.0",
    "User-Agent": "GitHubCopilotChat/0.22.0",
    "Copilot-Integration-Id": "vscode-chat",
}


@dataclass
class DeviceCodeInfo:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class CopilotProvider:
    name = "github_copilot"

    def __init__(
        self,
        *,
        github_token: str | None = None,  # token persistente si ya lo tienes
        token_store_path: str | None = None,  # opcional: guardar el github_token
        timeout: float = 60.0,
    ):
        self._github_token = github_token
        self._token_store_path = token_store_path

        self._copilot_token: str | None = None
        self._copilot_token_expiry: float = 0.0

        self._client = httpx.AsyncClient(timeout=timeout)

    # ---------- Device flow ----------

    async def start_device_flow(self) -> DeviceCodeInfo:
        """Paso 1: pedir device code. Devuelve info para mostrar al usuario."""
        resp = await self._client.post(
            "https://github.com/login/device/code",
            headers={"Accept": "application/json", **EDITOR_HEADERS},
            data={"client_id": VSCODE_CLIENT_ID, "scope": "read:user"},
        )
        if resp.status_code >= 400:
            raise AuthError(f"device/code falló: {resp.text}")
        d = resp.json()
        return DeviceCodeInfo(
            device_code=d["device_code"],
            user_code=d["user_code"],
            verification_uri=d["verification_uri"],
            expires_in=d["expires_in"],
            interval=d["interval"],
        )

    async def poll_device_flow(self, info: DeviceCodeInfo) -> str:
        """Paso 2-3: poll hasta que el usuario autorice. Devuelve github_token."""
        deadline = time.time() + info.expires_in
        interval = info.interval
        while time.time() < deadline:
            await asyncio.sleep(interval)
            resp = await self._client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json", **EDITOR_HEADERS},
                data={
                    "client_id": VSCODE_CLIENT_ID,
                    "device_code": info.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            d = resp.json()
            if "access_token" in d:
                self._github_token = d["access_token"]
                if self._token_store_path:
                    self._save_github_token()
                return d["access_token"]
            err = d.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err in ("expired_token", "access_denied"):
                raise AuthError(f"Device flow abortado: {err}")
        raise AuthError("Device flow expirado sin autorización")

    async def authenticate_interactive(
        self,
        on_user_code: Callable[[DeviceCodeInfo], Awaitable[None]] | None = None,
    ) -> str:
        """Helper que junta los dos pasos. El callback recibe el user_code para
        que tu app lo enseñe (CLI, web, etc.)."""
        info = await self.start_device_flow()
        if on_user_code:
            await on_user_code(info)
        else:
            print(f"Abre {info.verification_uri} e introduce: {info.user_code}")
        return await self.poll_device_flow(info)

    def _save_github_token(self) -> None:
        assert self._token_store_path and self._github_token
        with open(self._token_store_path, "w") as f:
            json.dump({"github_token": self._github_token}, f)

    # ---------- Copilot token (corto, refrescable) ----------

    async def _ensure_copilot_token(self) -> str:
        if self._copilot_token and time.time() < self._copilot_token_expiry - 60:
            return self._copilot_token
        if not self._github_token:
            raise AuthError("No hay github_token; ejecuta authenticate_interactive()")

        resp = await self._client.get(
            "https://api.github.com/copilot_internal/v2/token",
            headers={
                "Authorization": f"token {self._github_token}",
                "Accept": "application/json",
                **EDITOR_HEADERS,
            },
        )
        if resp.status_code == 401:
            raise AuthError("github_token inválido o sin acceso a Copilot")
        if resp.status_code >= 400:
            raise ProviderError(f"copilot_internal/v2/token: {resp.text}")
        d = resp.json()
        self._copilot_token = d["token"]
        self._copilot_token_expiry = float(d.get("expires_at", time.time() + 1500))
        return self._copilot_token

    # ---------- Chat ----------

    def _to_openai_messages(self, messages: Sequence[Message]) -> list[dict[str, Any]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    async def _chat_headers(self) -> dict[str, str]:
        token = await self._ensure_copilot_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Openai-Intent": "conversation-panel",
            **EDITOR_HEADERS,
        }

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> CompletionResponse:
        body = {
            "model": model or "gpt-4o",
            "messages": self._to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            **kwargs,
        }
        resp = await self._client.post(
            "https://api.githubcopilot.com/chat/completions",
            headers=await self._chat_headers(),
            json=body,
        )
        if resp.status_code == 401:
            # token expirado; invalida y reintenta una vez
            self._copilot_token = None
            resp = await self._client.post(
                "https://api.githubcopilot.com/chat/completions",
                headers=await self._chat_headers(),
                json=body,
            )
        if resp.status_code == 429:
            raise RateLimitError(resp.text)
        if resp.status_code >= 400:
            raise ProviderError(resp.text, status_code=resp.status_code)

        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage_d = data.get("usage", {}) or {}
        return CompletionResponse(
            content=content,
            model=body["model"],
            provider=self.name,
            usage=Usage(
                input_tokens=usage_d.get("prompt_tokens", 0),
                output_tokens=usage_d.get("completion_tokens", 0),
            ),
            raw=data,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        body = {
            "model": model or "gpt-4o",
            "messages": self._to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        async with self._client.stream(
            "POST",
            "https://api.githubcopilot.com/chat/completions",
            headers=await self._chat_headers(),
            json=body,
        ) as resp:
            if resp.status_code >= 400:
                text = await resp.aread()
                raise ProviderError(text.decode(), status_code=resp.status_code)
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    yield StreamChunk(delta="", done=True)
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices", [{}])[0].get("delta", {}).get("content")) or ""
                if delta:
                    yield StreamChunk(delta=delta)

    async def aclose(self) -> None:
        await self._client.aclose()
