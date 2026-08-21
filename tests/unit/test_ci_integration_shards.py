"""El reparto en shards de la suite de integración cubre TODOS los ficheros.

El job «Integration tests» de CI se partió en cuatro shards el 2026-08-19 porque
en uno solo nunca terminaba: llegaba al 45 % de la suite y agotaba su reloj de 45
minutos, y GitHub marcaba el job como ``cancelled`` — que no es ni verde ni rojo.
El PR llevaba semanas sin evidencia de integración sin que nadie lo notara.

Partir tiene un modo de fallo propio y es **peor que el problema que resuelve**:
un reparto que se deje ficheros fuera deja CI en verde probando menos, y no hay
nada en la salida que lo delate — los shards que sí corren dicen «passed» igual.

Este fichero reproduce la MISMA fórmula que el workflow (``find | sort`` +
módulo) sobre el árbol real y afirma las dos propiedades que hacen que el reparto
valga:

* **exhaustivo** — la unión de los cuatro shards es exactamente el conjunto de
  ficheros de ``tests/integration/``;
* **disjunto** — ningún fichero corre en dos shards, o el tiempo que se gana
  partiendo se pierde repitiendo.

Si alguien cambia el número de shards en el workflow, cambia ``_SHARDS`` aquí y
los tests siguen valiendo. Si cambia la FÓRMULA, este fichero deja de describir
lo que CI hace — y para eso está ``test_the_workflow_still_uses_this_formula``.
"""

from __future__ import annotations

import re
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
_INTEGRACION = _RAIZ / "tests" / "integration"
_WORKFLOW = _RAIZ / ".github" / "workflows" / "ci.yml"

#: Tiene que coincidir con `matrix.shard` y con `SHARDS` en `ci.yml`.
_SHARDS = 4


def _todos_los_ficheros() -> list[str]:
    """`find tests/integration -type f -name 'test_*.py' | sort`, en Python.

    El orden importa: es lo que hace el reparto determinista entre los cuatro
    jobs, que no se hablan entre sí. Se normaliza a `/` para que el resultado no
    dependa del sistema de ficheros.
    """
    encontrados = [
        str(p.relative_to(_RAIZ)).replace("\\", "/")
        for p in _INTEGRACION.rglob("test_*.py")
        if p.is_file()
    ]
    return sorted(encontrados)


def _shard(indice: int, shards: int = _SHARDS) -> list[str]:
    todos = _todos_los_ficheros()
    return [f for i, f in enumerate(todos) if i % shards == indice]


def test_the_discovery_finds_the_suite() -> None:
    """No-vacuidad: sin esto, un descubrimiento roto haría pasar todo lo demás.

    Con cero ficheros, «la unión es igual al conjunto» y «las intersecciones son
    vacías» son ciertas y no significan nada.
    """
    todos = _todos_los_ficheros()
    assert len(todos) >= 300, (
        f"esperaba cientos de ficheros en tests/integration/, encontré {len(todos)}."
        " O el descubrimiento se rompió, o la suite adelgazó de una forma que hay"
        " que mirar."
    )


def test_the_shards_cover_every_file() -> None:
    """Exhaustivo: ni un fichero se queda sin correr en ningún shard."""
    todos = set(_todos_los_ficheros())
    union: set[str] = set()
    for i in range(_SHARDS):
        union |= set(_shard(i))

    faltan = todos - union
    assert not faltan, (
        f"{len(faltan)} ficheros de integración no los corre NINGÚN shard:"
        f" {sorted(faltan)[:10]}.\nUn reparto incompleto deja CI en verde probando"
        " menos, y nada en la salida lo delata."
    )
    assert union == todos


def test_no_file_runs_twice() -> None:
    """Disjunto: lo que se gana partiendo no se pierde repitiendo."""
    vistos: dict[str, int] = {}
    for i in range(_SHARDS):
        for fichero in _shard(i):
            if fichero in vistos:
                raise AssertionError(
                    f"{fichero} corre en el shard {vistos[fichero]} y también en el {i}"
                )
            vistos[fichero] = i
    assert len(vistos) == len(_todos_los_ficheros())


def test_the_shards_are_reasonably_balanced() -> None:
    """Ninguno se lleva el doble que otro: el reloj lo marca el más lento.

    Con `módulo` sobre una lista ordenada el desequilibrio en NÚMERO de ficheros
    es como mucho de uno. Lo que este test no puede prometer —y conviene decirlo—
    es equilibrio en TIEMPO: un shard con tres ficheros lentos puede tardar el
    doble que otro con veinte rápidos. Si eso pasa, la salida a mirar es repartir
    por duración medida, no por cuenta.
    """
    tamanos = [len(_shard(i)) for i in range(_SHARDS)]
    assert min(tamanos) > 0, f"algún shard quedó vacío: {tamanos}"
    assert max(tamanos) - min(tamanos) <= 1, (
        f"el reparto por módulo debería diferir en un fichero como mucho: {tamanos}"
    )


def test_the_workflow_still_uses_this_formula() -> None:
    """Si CI cambia la fórmula, este fichero deja de describir lo que CI hace.

    Es la mitad que impide que este test se convierta en el que fija su propia
    idea del mundo: comprueba que en `ci.yml` siguen estando las tres piezas de
    las que dependen los tests de arriba (el `find | sort`, el módulo, y el
    número de shards).
    """
    assert _WORKFLOW.is_file(), f"no encuentro {_WORKFLOW}"
    texto = _WORKFLOW.read_text(encoding="utf-8")

    assert 'find tests/integration -type f -name "test_*.py" | sort' in texto, (
        "el workflow ya no descubre los ficheros con `find | sort`: el reparto de"
        " este test dejó de reproducir el suyo"
    )
    assert "i % SHARDS" in texto, "el workflow ya no reparte por módulo"

    declarados = re.search(r'SHARDS:\s*"(\d+)"', texto)
    assert declarados, "no encuentro `SHARDS:` en el workflow"
    assert int(declarados.group(1)) == _SHARDS, (
        f"el workflow declara {declarados.group(1)} shards y este test asume"
        f" {_SHARDS}. Cambia `_SHARDS` aquí."
    )

    matriz = re.search(r"shard:\s*\[([0-9a-z,\s]+)\]", texto)
    assert matriz, "no encuentro la matriz `shard:` en el workflow"
    entradas = [v.strip() for v in matriz.group(1).split(",")]
    valores = [int(v) for v in entradas if v.isdigit()]
    assert valores == list(range(_SHARDS)), (
        f"los shards numéricos de la matriz son {valores}; con `i % {_SHARDS}`"
        f" los índices tienen que ser exactamente {list(range(_SHARDS))} o hay"
        " ficheros que no corre nadie"
    )
    assert "gate" in entradas, (
        "la matriz ya no trae la entrada `gate`. No es un quinto shard: es el"
        " job que corre la puerta cross-tenant y tests/migrations. Si vuelve a"
        " colgar del shard 0, vuelve el acoplamiento que la dejó `skipped`"
        " (ver test_the_gate_does_not_hang_off_a_shard)"
    )


def test_the_gate_does_not_hang_off_a_shard() -> None:
    """La puerta y las migraciones corren en su job, no detrás de un cuarto.

    Vivieron colgadas del shard 0 hasta el 2026-08-20, y el run 32306122292
    enseñó lo que eso cuesta: el cuarto de la suite agotó el reloj del job, y
    los 33 tests de ``tests/migrations`` —que corrían DESPUÉS— quedaron
    ``skipped`` sin que nada lo dijera. Una puerta que sólo habla cuando su
    vecino acaba bien se calla justo cuando hace falta.

    Este test fija esa separación. Si alguien devuelve cualquiera de los dos
    pasos a ``matrix.shard == 0``, aquí se entera.
    """
    texto = _WORKFLOW.read_text(encoding="utf-8")

    for paso in ("pytest cross-tenant isolation gate", "pytest tests/migrations"):
        i = texto.find(f"- name: {paso}")
        assert i != -1, f"no encuentro el paso «{paso}» en el workflow"
        condicion = texto[i : i + 400]
        assert "matrix.shard == 'gate'" in condicion, (
            f"«{paso}» ya no está condicionado a `matrix.shard == 'gate'`."
            " Si vuelve a un shard numérico, hereda sus fallos: el día que ese"
            " shard agote el reloj, este paso no corre y el job no lo dice."
        )

    j = texto.find("- name: pytest tests/integration (shard")
    assert j != -1, "no encuentro el paso del cuarto de la suite"
    assert "matrix.shard != 'gate'" in texto[j : j + 400], (
        "el cuarto de la suite ya no excluye al job `gate`: correría un quinto"
        " reparto que ningún índice de `i % 4` produce, o sea nada, gastando un"
        " stack entero para no probar ni un fichero"
    )
