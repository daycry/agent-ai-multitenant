"""Informe de reutilización de caché de prompt por proveedor (`task_wf_63`).

La tarea es de MEDICIÓN, no de optimización, y el orden importa: `_decide_messages`
reconstruye un mensaje de usuario grande cada turno, y pasarlo a una lista
incremental —lo que permitiría a los proveedores con caché automática por
prefijo aprovechar también el histórico— es un cambio con riesgo real sobre la
convergencia. Antes de tocarlo hay que saber si sirve de algo.

Nota honesta que ya dejaba escrita la tarea: el catálogo (ADR 0021) no incluye
la Messages API de Anthropic en crudo, así que **no se aplica un `cache_control`
explícito**. La ganancia depende de la caché automática de cada proveedor y
**puede ser pequeña**. Este módulo no promete un ahorro: dice cuál es.

Se calcula sobre los `steps_log` que ya se persisten — sin tabla nueva, sin
telemetría paralela y sin coste en el camino caliente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderCacheStats:
    """Lo medido para UN proveedor."""

    provider: str
    model_calls: int = 0
    runs: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    # Cuántas llamadas reportaron caché. Distinto de «cuánta caché hubo»: un
    # proveedor que nunca la reporta y otro que la reporta siempre a cero son
    # dos situaciones distintas, y confundirlas llevaría a optimizar a ciegas.
    calls_reporting_cache: int = 0

    @property
    def cached_prefix_pct(self) -> float:
        if self.prompt_tokens <= 0:
            return 0.0
        return round(100.0 * self.cached_prompt_tokens / self.prompt_tokens, 2)

    @property
    def cost_per_iteration_usd(self) -> float:
        if self.model_calls <= 0:
            return 0.0
        return round(self.cost_usd / self.model_calls, 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_calls": self.model_calls,
            "runs": self.runs,
            "prompt_tokens": self.prompt_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cost_per_iteration_usd": self.cost_per_iteration_usd,
            "cached_prefix_pct": self.cached_prefix_pct,
            "calls_reporting_cache": self.calls_reporting_cache,
            # Sin una sola llamada que reporte caché no se puede concluir «no
            # hay reutilización»: puede que el proveedor simplemente no lo diga.
            # Decirlo evita que el informe se lea como un cero real.
            "reports_cache": self.calls_reporting_cache > 0,
        }


@dataclass
class PromptCacheReport:
    by_provider: dict[str, ProviderCacheStats] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        rows = sorted(self.by_provider.values(), key=lambda s: -s.model_calls)
        return {
            "providers": [row.as_dict() for row in rows],
            "total_model_calls": sum(row.model_calls for row in rows),
        }


def build_prompt_cache_report(runs: list[tuple[str | None, list[Any]]]) -> PromptCacheReport:
    """Agrega los `steps_log` de varios runs en el informe por proveedor.

    ``runs`` es ``[(provider_hint, steps_log), …]``. El `provider_hint` es el
    del run (cuando el llamante lo tiene a mano); si un step trae el suyo, gana
    el del step — es el que de verdad atendió esa llamada.
    """
    report = PromptCacheReport()
    for provider_hint, steps in runs:
        seen_in_run: set[str] = set()
        for step in steps or []:
            if not isinstance(step, dict) or step.get("kind") != "model_call":
                continue
            provider = str(step.get("provider") or provider_hint or "desconocido")
            stats = report.by_provider.setdefault(provider, ProviderCacheStats(provider=provider))
            stats.model_calls += 1
            stats.prompt_tokens += int(step.get("tokens_in") or 0)
            stats.output_tokens += int(step.get("tokens_out") or 0)
            stats.cost_usd += float(step.get("cost_usd") or 0.0)
            cached = int(step.get("cache_read_tokens") or 0)
            if cached:
                stats.cached_prompt_tokens += cached
                stats.calls_reporting_cache += 1
            if provider not in seen_in_run:
                seen_in_run.add(provider)
                stats.runs += 1
    return report
