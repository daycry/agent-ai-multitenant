---
name: gotcha-caplog-orden-tests
description: tests que afirman sobre logs vía caplog son frágiles por orden — la suite de la app desactiva propagación/logging.disable; usar logger fake monkeypatcheado.
metadata:
  node_type: memory
  type: feedback
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

> **DOCUMENTADO EN EL REPO (2026-07-26)**: `docs/03-guides/gotchas/tests-caplog-vs-logging-disable.md`. La fuente de verdad es esa; esta nota queda como puntero.

Un test unit que afirmaba sobre un log con `caplog` (pytest) pasaba en aislado
pero FALLABA en la suite completa cuando corría DESPUÉS de los tests de
integración (p.ej. `tests/integration/test_budget_pause.py`): el record no se
captura. Causa raíz: otras suites cargan el logging de la app, que puede llamar
`logging.disable(...)` (suprime niveles globalmente, ignora handlers) o matar la
propagación al root, de modo que `caplog` (handler en el root) no ve el record.
Ni `caplog.at_level(level, logger=name)` ni un handler propio en el logger lo
arreglan si hay `logging.disable`.

**Why:** `tests/integration` corre antes que `tests/unit` (orden alfabético), así
que el fallo es determinista en full-suite aunque "flaky" en aislado.

**How to apply:** para afirmar que algo se logueó, NO uses caplog/stdlib en este
repo. Monkeypatchea el `logger` del módulo con un fake que registra las llamadas
(`monkeypatch.setattr("modulo.logger", fake)`), independiente del estado global
de logging. Ver `tests/unit/test_parse_cron_loud_failure.py::_RecordingLogger`.
Relacionado con [[workflow-review-paralelo-contamina-fuente]] (contaminación entre
unidades de test).
