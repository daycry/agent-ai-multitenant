---
title: "`caplog` no ve el record cuando la suite completa corre integración antes que unit"
area: tests, python
encountered: 2026-07-05
stack: pytest 8.x, logging stdlib, structlog
---

## Síntoma

Un test unit que afirma sobre un log con `caplog` pasa **en aislado** y falla en la
**suite completa**:

```
assert "budget.paused" in caplog.text
AssertionError: assert '...' in ''
```

El fallo es determinista, no intermitente: `tests/integration` corre antes que
`tests/unit` por orden alfabético, así que en full-suite falla siempre.

## Causa raíz

Otras suites cargan el logging de la aplicación, que puede llamar
`logging.disable(...)` — suprime niveles **globalmente** e ignora los handlers— o
cortar la propagación al logger root. `caplog` instala su handler en el root, así
que deja de ver el record.

Ni `caplog.at_level(level, logger=name)` ni añadir un handler propio al logger lo
arreglan: `logging.disable` actúa antes que cualquier handler.

## Fix

No usar `caplog` para afirmar que algo se logueó. Sustituir el logger del módulo
por un doble que registra las llamadas:

```python
class _RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw: object) -> None:
        self.calls.append((event, kw))

def test_it_logs_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingLogger()
    monkeypatch.setattr("mi_modulo._log", fake)
    ...
    assert any(event == "parse_cron.failed" for event, _ in fake.calls)
```

Es además una aserción mejor: comprueba el **evento** y sus campos, no una
subcadena de texto renderizado.

## Cómo verificar el fix

`python -m pytest tests/ -q` (suite completa, sin `-p no:randomly`) y el mismo
fichero en aislado dan el mismo resultado. Ejemplo vivo:
`tests/unit/test_parse_cron_loud_failure.py::_RecordingLogger`.
