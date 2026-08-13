"""Los healthchecks del compose canónico deben poder FALLAR (prod-08, deploy-9).

Hallazgo `deploy-9` de la auditoría de producción: el healthcheck del
egress-proxy terminaba en ``|| true``.

    wget -q -O- --no-proxy http://127.0.0.1:8888/ 2>&1 | grep -q tinyproxy || true

Un test de salud que termina en ``|| true`` **siempre devuelve 0**. El
contenedor aparecía ``healthy`` con tinyproxy muerto, y como el egress-proxy es
la ÚNICA salida de los agent-runtimes hacia los proveedores LLM (ADR 0019),
los agentes se quedaban sin red sin que el stack delatara la causa: ni
``docker ps`` ni el watchdog ni una futura regla ``ServiceDown`` podían verlo.

Es el mismo defecto que «una guarda que no puede fallar no es una guarda»
(``docs/03-guides/verificar-antes-de-implementar.md`` §4), aplicado a la
infraestructura.

Este test es una guarda estática, así que lleva su propia aserción de que
**encontró algo**: el día que el descubrimiento deje de ver healthchecks, falla
en vez de pasar en vacío.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_COMPOSE = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"


def _healthcheck_tests() -> dict[str, str]:
    """{nombre de servicio: comando del healthcheck} del compose canónico."""
    raw = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for name, service in (raw.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        healthcheck = service.get("healthcheck")
        if not isinstance(healthcheck, dict):
            continue
        test = healthcheck.get("test")
        if isinstance(test, list):
            found[name] = " ".join(str(part) for part in test)
        elif isinstance(test, str):
            found[name] = test
    return found


def test_no_healthcheck_swallows_its_own_failure() -> None:
    checks = _healthcheck_tests()

    # Guarda contra el paso en vacío (§4): si el parser deja de encontrar
    # healthchecks, este test debe FALLAR, no aprobar por silencio.
    assert len(checks) >= 5, (
        f"la guarda dejó de encontrar healthchecks en {_COMPOSE.name} (vio {len(checks)})"
    )

    offenders = sorted(name for name, test in checks.items() if "|| true" in test)
    assert not offenders, (
        "estos healthchecks se tragan su propio fallo con `|| true`, así que el "
        f"servicio sale healthy aunque esté muerto: {offenders}"
    )


def test_the_two_tinyproxy_proxies_are_covered_and_fail_loudly() -> None:
    """Los dos proxies del hallazgo, nombrados explícitamente.

    Sin nombrarlos, un renombrado del servicio dejaría el test verde mientras
    el `|| true` vuelve por la puerta de atrás.
    """
    checks = _healthcheck_tests()

    for name in ("egress-proxy", "registry-proxy"):
        assert name in checks, f"{name} perdió su healthcheck"
        test = checks[name]
        assert "|| true" not in test
        assert "|| exit 1" in test, (
            f"el healthcheck de {name} debe terminar en `|| exit 1` para que un "
            "proxy muerto se reporte unhealthy"
        )
        # `-Y off`, no `--no-proxy`: la imagen lleva el wget de BusyBox, que NO
        # reconoce esa opción. Con ella, el comando salía por el mensaje de uso
        # y el healthcheck no podía pasar NUNCA — invisible mientras hubo un
        # `|| true` delante. Al retirarlo, los dos proxies salieron `unhealthy`
        # en el primer despliegue (2026-08-10) y lo roto era la comprobación.
        assert "--no-proxy" not in test, (
            f"el healthcheck de {name} usa `--no-proxy`, que el wget de BusyBox "
            "no reconoce: saldría por usage y nunca podría pasar. Usa `-Y off`"
        )
        # Se afirma el 403 y no la palabra «tinyproxy»: el cuerpo de la página de
        # error no viaja con `-q`, pero la línea de estado sí. Un 403 a una
        # petición DIRECTA prueba que el demonio escucha y aplica su política;
        # caído, wget diría «Connection refused».
        assert "403" in test, (
            f"el healthcheck de {name} debe afirmar el 403 que tinyproxy da a "
            "una petición directa: es la señal de que está vivo y filtrando"
        )
