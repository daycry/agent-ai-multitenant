"""El disparador del ADR 0152 tiene que ser comprobable, o es literatura.

El [ADR 0152](../../docs/05-architecture-decisions/0152-recall-vectorial-multitenant-hnsw.md)
decidió «A ahora, C cuando se cumpla un disparador medible», y escribió el
disparador con número:

> Pasar a C cuando **el tenant más grande supere el 60 % de los chunks con
> embedding de la plataforma** y el corpus total pase de ~200.000 chunks.

Y luego dijo lo que faltaba para poder tomarla: *«una métrica del reparto del
corpus por tenant. Sin ella el disparador de arriba no es comprobable y este ADR
se queda en literatura.»* Esto es esa métrica.

**Por qué se testea el renderizado y no la consulta.** Las dos mitades fallan de
forma distinta: la SQL falla ruidosamente (una tabla que no está, una columna
que cambió) y la aritmética falla en silencio — una fracción calculada sobre el
total equivocado publica un 0,4 donde había un 0,7 y nadie lo nota, que es
exactamente el modo de fallo que el ADR persigue. Por eso el cálculo vive en una
función pura y aquí se le mide el borde: corpus vacío, un solo tenant, empate.
"""

from __future__ import annotations

import pytest
from workers.corpus_distribution import (
    METRIC_LARGEST_SHARE,
    METRIC_TENANTS,
    METRIC_TOTAL,
    render_corpus_distribution,
)

pytestmark = pytest.mark.unit


def _valor(cuerpo: str, metrica: str) -> float:
    """La última línea que no es comentario para esa métrica."""
    for linea in cuerpo.splitlines():
        if linea.startswith(f"{metrica} "):
            return float(linea.split(" ", 1)[1])
    raise AssertionError(f"{metrica} no aparece en:\n{cuerpo}")


def test_el_reparto_se_publica_con_las_tres_cifras_del_disparador() -> None:
    cuerpo = render_corpus_distribution(por_tenant=[300, 150, 50])
    assert _valor(cuerpo, METRIC_TOTAL) == 500
    assert _valor(cuerpo, METRIC_TENANTS) == 3
    assert _valor(cuerpo, METRIC_LARGEST_SHARE) == pytest.approx(0.6)


def test_un_corpus_vacio_no_divide_entre_cero() -> None:
    """El caso del día 1, y el que rompe una fracción escrita a la ligera."""
    cuerpo = render_corpus_distribution(por_tenant=[])
    assert _valor(cuerpo, METRIC_TOTAL) == 0
    assert _valor(cuerpo, METRIC_TENANTS) == 0
    assert _valor(cuerpo, METRIC_LARGEST_SHARE) == 0.0


def test_un_solo_tenant_es_el_100_por_cien_y_no_dispara_por_si_solo() -> None:
    """El desequilibrio máximo con corpus pequeño NO cumple el disparador.

    El ADR es explícito en que son las dos condiciones a la vez: «el
    desequilibrio sin volumen lo absorbe `iterative_scan` sin que se note». La
    métrica publica el reparto; quien decide es la regla que las combina.
    """
    cuerpo = render_corpus_distribution(por_tenant=[42])
    assert _valor(cuerpo, METRIC_LARGEST_SHARE) == 1.0
    assert _valor(cuerpo, METRIC_TOTAL) == 42


def test_el_reparto_perfecto_publica_la_cuota_del_mayor_no_la_media() -> None:
    """Con cuatro tenants iguales la cuota del mayor es 0,25 — no 1/4 de nada más.

    Parece una obviedad y no lo es: publicar la media en vez del máximo daría
    una cifra que baja al crecer el número de tenants, o sea que enmascararía
    justo el caso que el disparador busca.
    """
    cuerpo = render_corpus_distribution(por_tenant=[25, 25, 25, 25])
    assert _valor(cuerpo, METRIC_LARGEST_SHARE) == pytest.approx(0.25)


def test_el_orden_de_entrada_no_cambia_el_resultado() -> None:
    """La consulta agrupa sin ORDER BY; el cálculo no puede depender de eso."""
    a = render_corpus_distribution(por_tenant=[10, 90])
    b = render_corpus_distribution(por_tenant=[90, 10])
    assert _valor(a, METRIC_LARGEST_SHARE) == _valor(b, METRIC_LARGEST_SHARE) == pytest.approx(0.9)


def test_el_cuerpo_lleva_HELP_y_TYPE_de_cada_metrica() -> None:
    """Sin cabeceras, node-exporter la sirve igual y Grafana la muestra sin unidades."""
    cuerpo = render_corpus_distribution(por_tenant=[1, 2])
    for metrica in (METRIC_TOTAL, METRIC_TENANTS, METRIC_LARGEST_SHARE):
        assert f"# HELP {metrica} " in cuerpo
        assert f"# TYPE {metrica} gauge" in cuerpo


def test_los_recuentos_negativos_son_un_error_de_programacion_y_no_se_publican() -> None:
    """Un COUNT no puede ser negativo: si llega uno, la consulta cambió de forma.

    Publicar una cuota calculada sobre eso sería peor que no publicar nada.
    """
    with pytest.raises(ValueError):
        render_corpus_distribution(por_tenant=[10, -1])
