"""Córtex F4 — métricas del bucle de curiosidad (ADR 0078, Sub-fase 4.6).

Las cuatro métricas que el plan pedía no existían en absoluto: el bucle autónomo
gastaba dinero y egress y **no dejaba ni una serie temporal**. Sin ellas, la única
forma de saber si la curiosidad funciona era abrir la BD y contar filas de
`cortex_curiosity_pursuits` a mano — y, peor, un bucle silencioso porque nadie
encendió el kill-switch era indistinguible de un bucle silencioso porque el
circuit-breaker está abierto.

Se publican por el **textfile-collector de node-exporter**, el mismo patrón
dependency-free de `backup_metrics` (render puro + escritura atómica por
`textfile_collector.write_textfile_metric`, que ya trata el sink ausente como
postura esperada y no como fallo).

Lo que estos tests fijan:

  * **render determinista** — mismo estado ⇒ mismo texto, ordenado, sin ruido de
    diff (el fichero se reescribe en cada pasada);
  * **los contadores ACUMULAN** — un `*_total` que se reinicia en cada pasada no es
    un contador: `rate()` daría cero y las alertas no armarían nunca. El fichero es
    el único estado que hay, así que hay que leer lo publicado y sumar;
  * **no-op seguro sin el dir del collector** — sin la pila de monitorización el
    bucle no puede fallar por no poder contarse;
  * **`outcome_of` es puro y cubre las ramas del gate** — es lo que convierte el
    dict de retorno del bucle en la etiqueta, y su conjunto de valores es CERRADO
    (Prometheus no perdona la cardinalidad abierta).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from workers.cortex_curiosity_metrics import (
    KNOWN_OUTCOMES,
    METRIC_CIRCUIT_OPEN,
    METRIC_COST_USD,
    METRIC_RUNS,
    METRIC_SEARCHES,
    circuit_open_from_result,
    outcome_of,
    render_curiosity_metrics,
    write_curiosity_metrics,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Render puro
# ---------------------------------------------------------------------------
def test_render_emite_las_cuatro_metricas_con_sus_tipos() -> None:
    """Las cuatro del plan, con HELP/TYPE y la etiqueta `outcome` en los runs."""
    body = render_curiosity_metrics(
        runs_by_outcome={"digested": 3, "budget": 1},
        cost_usd_total=0.1234,
        searches_total=7,
        circuit_open=False,
    )

    assert f"# TYPE {METRIC_RUNS} counter" in body
    assert f"# TYPE {METRIC_COST_USD} counter" in body
    assert f"# TYPE {METRIC_SEARCHES} counter" in body
    assert f"# TYPE {METRIC_CIRCUIT_OPEN} gauge" in body
    assert f'{METRIC_RUNS}{{outcome="digested"}} 3' in body
    assert f'{METRIC_RUNS}{{outcome="budget"}} 1' in body
    assert f"{METRIC_SEARCHES} 7" in body
    assert f"{METRIC_CIRCUIT_OPEN} 0" in body
    # El coste lleva decimales: redondearlo a entero perdería TODO el gasto real
    # (una pasada cuesta céntimos).
    assert f"{METRIC_COST_USD} 0.123400" in body
    assert body.endswith("\n")


def test_render_es_determinista_y_ordenado() -> None:
    """Mismo estado ⇒ mismo texto; etiquetas en orden alfabético.

    El fichero se reescribe entero en cada pasada: sin orden estable, el diff
    cambiaría sin que cambiase nada y cualquier comparación (o revisión humana del
    `.prom`) sería ruido."""
    a = render_curiosity_metrics(
        runs_by_outcome={"skipped": 1, "digested": 2},
        cost_usd_total=0.5,
        searches_total=1,
        circuit_open=True,
    )
    b = render_curiosity_metrics(
        runs_by_outcome={"digested": 2, "skipped": 1},
        cost_usd_total=0.5,
        searches_total=1,
        circuit_open=True,
    )
    assert a == b
    assert a.index('outcome="digested"') < a.index('outcome="skipped"')
    assert f"{METRIC_CIRCUIT_OPEN} 1" in a


def test_render_con_el_breaker_abierto_pone_el_gauge_a_uno() -> None:
    """Aceptación literal del plan: «el circuit-breaker abierto pone el gauge a 1»."""
    body = render_curiosity_metrics(
        runs_by_outcome={}, cost_usd_total=0.0, searches_total=0, circuit_open=True
    )
    assert f"{METRIC_CIRCUIT_OPEN} 1" in body


def test_render_sin_pasadas_sigue_siendo_valido() -> None:
    """Arranque en frío: cero runs ⇒ fichero válido con las cabeceras y los totales.

    Si la ausencia de runs produjese un fichero sin las cabeceras, Prometheus vería
    desaparecer las series y las alertas de staleness armarían por la razón
    equivocada."""
    body = render_curiosity_metrics(
        runs_by_outcome={}, cost_usd_total=0.0, searches_total=0, circuit_open=False
    )
    assert f"# TYPE {METRIC_RUNS} counter" in body
    assert f"{METRIC_COST_USD} 0.000000" in body
    assert f"{METRIC_SEARCHES} 0" in body


def test_render_escapa_valores_de_etiqueta_raros() -> None:
    """Defensivo: un outcome con comillas no puede romper el formato de exposición."""
    body = render_curiosity_metrics(
        runs_by_outcome={'we"ird': 1}, cost_usd_total=0.0, searches_total=0, circuit_open=False
    )
    assert r'outcome="we\"ird"' in body


# ---------------------------------------------------------------------------
# outcome_of: el dict del bucle → la etiqueta (puro, conjunto CERRADO)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"digested": True, "topic": "rust"}, "digested"),
        ({"pending_approval": True, "pursuit_id": "x"}, "pending_approval"),
        ({"awaiting_approval": True, "pursuit_id": "x"}, "awaiting_approval"),
        ({"skipped": "disabled"}, "disabled"),
        ({"skipped": "curiosity_disabled"}, "curiosity_disabled"),
        ({"skipped": "web_disabled"}, "web_disabled"),
        ({"skipped": "circuit_open"}, "circuit_open"),
        ({"skipped": "drive_satisfied", "curiosity": 0.9}, "drive_satisfied"),
        ({"skipped": "budget", "reason": "usd_budget_exhausted"}, "budget"),
        ({"skipped": "no_topic"}, "no_topic"),
        ({"skipped": "no_owner"}, "no_owner"),
        ({"skipped": "empty_digest", "pursuit_id": "x"}, "empty_digest"),
        ({"failed": "research_error", "pursuit_id": "x"}, "failed"),
        ({"error": "boom"}, "error"),
    ],
)
def test_outcome_of_cubre_cada_rama_del_bucle(result: dict[str, object], expected: str) -> None:
    """Cada rama observable del bucle tiene su etiqueta, y son todas conocidas.

    La aceptación del plan dice «cada rama del gate observable»; si una rama nueva
    cayese en un genérico, el operador vería el bucle "haciendo algo" sin poder
    saber qué. El segundo assert impide que la etiqueta se salga del conjunto
    cerrado: Prometheus con cardinalidad abierta es una fuga de memoria."""
    assert outcome_of(result) == expected
    assert expected in KNOWN_OUTCOMES


def test_outcome_of_de_algo_inesperado_es_unknown_no_una_excepcion() -> None:
    """Un dict que no reconoce NO puede tumbar la pasada: `unknown` y a seguir.

    La métrica es contabilidad best-effort de un bucle de fondo; levantar aquí
    convertiría un cambio inocente del dict de retorno en un fallo de beat."""
    assert outcome_of({"algo": "nuevo"}) == "unknown"
    assert outcome_of({}) == "unknown"
    assert "unknown" in KNOWN_OUTCOMES


def test_circuit_open_se_deduce_del_resultado() -> None:
    """El gauge sale del propio resultado: sin consultar Redis por segunda vez.

    Los dos caminos por los que el breaker está abierto al acabar la pasada: o ya
    lo estaba (`skipped: circuit_open`) o lo acaba de abrir este fallo
    (`cb_opened`)."""
    assert circuit_open_from_result({"skipped": "circuit_open"}) is True
    assert circuit_open_from_result({"failed": "research_error", "cb_opened": True}) is True
    assert circuit_open_from_result({"failed": "research_error", "cb_opened": False}) is False
    assert circuit_open_from_result({"digested": True}) is False


# ---------------------------------------------------------------------------
# Escritura: acumula y es no-op seguro
# ---------------------------------------------------------------------------
def test_los_contadores_acumulan_entre_pasadas(tmp_path: Path) -> None:
    """Tres pasadas ⇒ los `*_total` suman; el fichero es el único estado.

    Es el requisito que hace que estas series sean CONTADORES de verdad. Si cada
    pasada reescribiese desde cero, `rate(agentic_cortex_curiosity_cost_usd_total)`
    daría 0 y el gasto acumulado del día sería invisible — justo el dato que
    justifica la métrica."""
    target = tmp_path / "collector" / "agentic_cortex_curiosity.prom"

    assert write_curiosity_metrics(
        target, outcome="digested", cost_usd=0.01, searches=1, circuit_open=False
    )
    assert write_curiosity_metrics(
        target, outcome="digested", cost_usd=0.02, searches=2, circuit_open=False
    )
    assert write_curiosity_metrics(
        target, outcome="budget", cost_usd=0.0, searches=0, circuit_open=False
    )

    body = target.read_text(encoding="utf-8")
    assert f'{METRIC_RUNS}{{outcome="digested"}} 2' in body
    assert f'{METRIC_RUNS}{{outcome="budget"}} 1' in body
    assert f"{METRIC_SEARCHES} 3" in body
    assert f"{METRIC_COST_USD} 0.030000" in body


def test_una_pasada_nueva_no_borra_los_outcomes_ya_publicados(tmp_path: Path) -> None:
    """La etiqueta que no se toca en esta pasada CONSERVA su cuenta.

    Sin esto, cada pasada dejaría una sola serie viva y el histórico por outcome
    («¿cuántas veces se saltó por budget esta semana?») se perdería en cada tick."""
    target = tmp_path / "agentic_cortex_curiosity.prom"
    write_curiosity_metrics(target, outcome="failed", cost_usd=0.0, searches=1, circuit_open=True)
    write_curiosity_metrics(
        target, outcome="digested", cost_usd=0.05, searches=1, circuit_open=False
    )

    body = target.read_text(encoding="utf-8")
    assert f'{METRIC_RUNS}{{outcome="failed"}} 1' in body
    assert f'{METRIC_RUNS}{{outcome="digested"}} 1' in body
    # El gauge NO acumula: refleja el estado ACTUAL (el breaker ya no está abierto).
    assert f"{METRIC_CIRCUIT_OPEN} 0" in body


def test_un_fichero_corrupto_no_impide_publicar(tmp_path: Path) -> None:
    """Basura en el `.prom` (o un truncado a medias) se ignora y se republica.

    El fichero lo puede pisar cualquiera (es un fichero suelto en un volumen
    compartido). Un parseo estricto convertiría eso en que la métrica no vuelve
    nunca; lo correcto es empezar la cuenta de nuevo y seguir emitiendo."""
    target = tmp_path / "agentic_cortex_curiosity.prom"
    target.write_text("esto no es prometheus\nagentic_cortex_curiosity_searches_total NaNaNa\n")

    assert write_curiosity_metrics(
        target, outcome="digested", cost_usd=0.01, searches=1, circuit_open=False
    )
    body = target.read_text(encoding="utf-8")
    assert f"{METRIC_SEARCHES} 1" in body


def test_publish_pass_metrics_traduce_un_resultado_real_del_bucle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El punto de entrada de la tarea Celery: del dict de la pasada al `.prom`.

    Es la mitad "¿quién lo llama?" de la feature. Este repositorio ya se ha
    encontrado varias veces con el mecanismo entregado y sin llamante (evals,
    `record_shadow_eval`…), así que se prueba el camino completo con la forma REAL
    que devuelve `_run_curiosity_loop` en su camino feliz — no con un dict
    inventado."""
    from workers.cortex_curiosity_metrics import METRICS_PATH_ENV, publish_pass_metrics

    target = tmp_path / "agentic_cortex_curiosity.prom"
    monkeypatch.setenv(METRICS_PATH_ENV, str(target))

    resultado_feliz = {
        "digested": True,
        "topic": "rust",
        "pursuit_id": "0198f0a0-0000-7000-8000-000000000000",
        "learning_memory_id": "0198f0a0-0000-7000-8000-000000000001",
        "search_count": 2,
        "cost_usd": 0.042,
    }
    assert publish_pass_metrics(resultado_feliz) is True

    body = target.read_text(encoding="utf-8")
    assert f'{METRIC_RUNS}{{outcome="digested"}} 1' in body
    assert f"{METRIC_SEARCHES} 2" in body
    assert f"{METRIC_COST_USD} 0.042000" in body
    assert f"{METRIC_CIRCUIT_OPEN} 0" in body

    # Y una pasada que abre el breaker deja el gauge a 1 (forma real de esa rama).
    assert (
        publish_pass_metrics({"failed": "research_error", "pursuit_id": "x", "cb_opened": True})
        is True
    )
    body = target.read_text(encoding="utf-8")
    assert f'{METRIC_RUNS}{{outcome="failed"}} 1' in body
    assert f"{METRIC_CIRCUIT_OPEN} 1" in body


def test_es_noop_seguro_si_el_dir_del_collector_no_existe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin sink de textfile la publicación devuelve False y NO levanta.

    La aceptación del plan: «emisión es no-op seguro si el dir del collector no
    existe». Se simula el sink no aprovisionado haciendo fallar el `mkdir` del
    helper compartido, que es exactamente cómo se manifiesta en un stack sin la
    pila de monitorización (EACCES sobre `/host`)."""
    calls: list[str] = []

    def _mkdir_boom(self: Path, *args: object, **kwargs: object) -> None:
        calls.append(str(self))
        raise PermissionError("[Errno 13] Permission denied: '/host'")

    monkeypatch.setattr(Path, "mkdir", _mkdir_boom)
    monkeypatch.setattr("workers.textfile_collector._reported_absent_sinks", set())

    ok = write_curiosity_metrics(
        tmp_path / "nope" / "x.prom",
        outcome="digested",
        cost_usd=0.01,
        searches=1,
        circuit_open=False,
    )
    assert ok is False
    assert calls  # se intentó de verdad (la guarda no pasa vacía)
