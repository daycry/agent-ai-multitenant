"""Córtex F2 (fase D) — settings del distilador afectivo + registro de su módulo.

La tarea «Settings del worker + registro del módulo» de
`docs/roadmap/cortex-f2-afectivo.md` pedía por nombre un test de que `Settings()`
expone `cortex_affect_llm_base_url` / `cortex_affect_llm_model` y de que el módulo
del distilador está en `app.conf.imports`. La auditoría del 2026-07-27
(`docs/roadmap/gaps-cortex-2026-07-27.md`) comprobó que no existía: el único
assert de este estilo en el repo era para otra tarea
(`tests/integration/test_human_task_escalation.py`, `workers.human_escalation`).

Los dos defectos que atrapa, y ninguno da error al arrancar:

  * **la task no se registra.** `imports` es la lista de módulos que el worker
    importa al bootear; una `@app.task` sólo se registra al importar su módulo. Si
    `"workers.cortex_affect"` desaparece de esa lista, el trigger post-turno sigue
    encolando `workers.cortex_distill_affect` sin quejarse y el mensaje muere en la
    cola con `NotRegistered`: el turno responde igual, el dial afectivo se queda
    congelado y nadie se entera hasta mirar el panel días después.
  * **el setting se queda muerto.** Un `Field` documentado como operator-tunable
    que nadie lee es peor que no tenerlo: el operador exporta la variable, reinicia
    el worker y no pasa nada (exactamente lo que pasó con los cron del córtex, ver
    `tests/unit/test_cortex_beat_schedule.py`). Aquí se comprueba que los dos
    campos llegan de verdad al provider que el distilador construye.

Además fija el **default LOCAL**: el appraisal corre contra Ollama en localhost
por diseño (ADR 0021 + ADR 0075, sin egress). Un default apuntando a un host
remoto convertiría cada turno del córtex en tráfico de salida silencioso.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str):
    from workers.config import Settings, reset_settings_cache

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    try:
        return Settings()
    finally:
        reset_settings_cache()


# ---------------------------------------------------------------------------
# Los dos campos existen, con default local sin egress
# ---------------------------------------------------------------------------
def test_settings_expone_los_dos_campos_del_distilador(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    assert isinstance(settings.cortex_affect_llm_base_url, str)
    assert isinstance(settings.cortex_affect_llm_model, str)
    assert settings.cortex_affect_llm_base_url
    assert settings.cortex_affect_llm_model


def test_el_default_del_distilador_es_ollama_local_sin_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El appraisal es barato y frecuente (un turno = una llamada): su default NO
    puede sacar tráfico de la máquina."""
    settings = _settings(monkeypatch)
    assert "localhost" in settings.cortex_affect_llm_base_url
    assert "11434" in settings.cortex_affect_llm_base_url  # puerto de `ollama serve`


def test_los_dos_campos_son_operator_tunables_por_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        WORKERS_CORTEX_AFFECT_LLM_BASE_URL="http://ollama-gestionado:11434/v1",
        WORKERS_CORTEX_AFFECT_LLM_MODEL="qwen3:4b",
    )
    assert settings.cortex_affect_llm_base_url == "http://ollama-gestionado:11434/v1"
    assert settings.cortex_affect_llm_model == "qwen3:4b"


# ---------------------------------------------------------------------------
# Los settings llegan al provider (no son Field decorativos)
# ---------------------------------------------------------------------------
def test_el_provider_del_distilador_sale_de_esos_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La otra mitad del criterio: cablear el `Field` y no leerlo dejaría al
    operador exportando una variable que no hace nada."""
    settings = _settings(
        monkeypatch,
        WORKERS_CORTEX_AFFECT_LLM_BASE_URL="http://appraisal-host:11434/v1",
        WORKERS_CORTEX_AFFECT_LLM_MODEL="modelo-de-appraisal",
    )
    from workers.cortex_affect import _default_llm_factory

    provider = _default_llm_factory(settings)
    assert provider.base_url.startswith("http://appraisal-host:11434")
    assert provider.default_model == "modelo-de-appraisal"


# ---------------------------------------------------------------------------
# Registro del módulo + de la task en la app Celery
# ---------------------------------------------------------------------------
def test_el_modulo_del_distilador_esta_en_los_imports_del_worker() -> None:
    """Sin este módulo en `imports`, el worker real bootea sin la task y el
    `apply_async` del trigger muere con `NotRegistered` — en silencio."""
    from workers.celery_app import app

    assert "workers.cortex_affect" in app.conf.imports


def test_la_task_del_distilador_se_registra_con_su_nombre_publico() -> None:
    """El nombre es el contrato con el trigger post-turno: si se renombra la task
    sin tocar el `send_task`, el afecto deja de distilarse sin ningún error."""
    import workers.cortex_affect  # noqa: F401  (registra cortex_distill_affect)
    from workers.celery_app import app

    assert "workers.cortex_distill_affect" in app.tasks
