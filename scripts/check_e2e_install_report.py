#!/usr/bin/env python
"""Gate del e2e de instalación: lee el JUnit XML y decide si el verde vale algo.

Lo invoca `.github/workflows/install-e2e.yml` JUSTO DESPUÉS de pytest, con
`if: always()`, sobre el informe que ese mismo paso escribe con `--junitxml`.

Por qué existe
--------------
`tests/e2e/test_install_from_scratch.py` está gateado por dos fixtures de sesión
encadenadas (`tests/e2e/conftest.py`): `E2E_INSTALL != "1"` -> `pytest.skip`, y
sin daemon Docker -> `pytest.skip`. El gate cae en el SETUP, no en la
recolección, y eso tiene una consecuencia que se lee mal: los casos se
recolectan, se saltan y **pytest sale 0**.

O sea que el paso de CI daría verde sin haber instalado nada. Y no es una
hipótesis: durante meses el compose generado montaba once rutas que nadie
creaba —Postgres nacía sin `pgvector` y sin sus roles— y este e2e, el único que
lo habría visto, se saltaba en verde en cada ejecución.

La red de seguridad que CI ya usa contra la suite vaciada NO cubre esto. Está
escrita dos veces en `ci.yml` (:320-322 y :755-761) y consiste en tratar el
`exit 5` de pytest —«no tests collected»— como fallo. Aquí se recolectan cuatro
casos: el exit es 0 y el hueco queda entero.

Qué comprueba, entonces
-----------------------
El informe máquina, por NODEID, no por recuento:

1. **El informe existe y se parsea.** Si pytest murió antes de escribirlo, no
   hay verde que discutir. Esta rama sólo significa lo que dice porque el
   workflow BORRA el fichero antes de invocar pytest: sin ese borrado, «existe»
   no probaría nada —en un self-hosted el workspace persiste, y el informe lo
   escribe root, así que ni el `git clean` del checkout lo quita— y el gate
   podría estar leyendo el veredicto de anteayer y anunciándolo como el de hoy.
2. **Los cuatro casos de `test_install_from_scratch` están** y ninguno lleva
   hijo ``<skipped>``. Un salto ahí es exactamente el falso verde.
3. **NINGÚN caso del informe está saltado.** Sin lista de tolerados a propósito
   —ver abajo—, y eso caza además el segundo falso verde, el de dentro: si el
   parseo del revelado no saca la contraseña,
   `test_admin_login_with_the_revealed_credential` se salta SOLO
   (`test_install_from_scratch.py:54-55`) y desaparece la única aserción que
   prueba que la credencial sembrada autentica.
4. **Suelo de descubrimiento** (`docs/03-guides/verificar-antes-de-implementar.md`
   §4): si el módulo encoge, el informe trae menos casos que los exigidos y esto
   falla en vez de pasar en vacío.
5. **Cero fallos y cero errores.** Redundante con el rc de pytest y a propósito:
   este script no puede ser nunca lo que diga «bien» sobre una suite roja.

Por qué NO hay lista de skips tolerados
---------------------------------------
La tentación era permitir el `pytest.skip()` incondicional de
`tests/e2e/test_worktree_execution.py:36`, que se dispara aunque todo esté en su
sitio. La salida es más simple y más firme: **el workflow invoca sólo
`tests/e2e/test_install_from_scratch.py`**, así que el informe no puede traer un
salto legítimo y la regla es cero. Cuatro commits atrás este repo escribió por
qué (f68acdcf): «una guarda que se salta el caso que no encaja en su forma
preferida no es una guarda con una excepción: es un agujero con buena
presentación».

Si algún día el paso amplía la invocación a `tests/e2e`, este script se pondrá
rojo — y ésa es la respuesta correcta: obliga a decidir a mano qué salto se
tolera y a escribir aquí por qué, en vez de heredarlo en silencio.

Uso
---
    pytest tests/e2e/test_install_from_scratch.py -v --junitxml=reports/e2e-install.xml
    python scripts/check_e2e_install_report.py reports/e2e-install.xml
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: Módulo cuyo veredicto acredita deploy-1/2/3 (plan prod-01, task_prod01_20).
#: El `classname` de JUnit es la ruta del módulo en puntos, relativa al rootdir.
REQUIRED_MODULE = "tests.e2e.test_install_from_scratch"

#: Los casos que TIENEN que haberse ejecutado. Escritos aquí y no derivados del
#: fichero porque este script corre en el runner, donde lo que hay es el informe;
#: quien impide que la lista envejezca es la guarda
#: `tests/unit/test_install_e2e_gate.py`, que lee el módulo con `ast` y compara.
#: Sin ella, añadir un quinto test al e2e lo dejaría fuera del gate sin que nada
#: avisara — que es la forma que tiene una guarda de encoger sin ruido.
REQUIRED_TESTS: tuple[str, ...] = (
    "test_install_completes_and_is_not_a_simulation",
    "test_proxy_serves_https_healthz",
    "test_direct_app_ports_are_not_published",
    "test_admin_login_with_the_revealed_credential",
)


def _load(path: Path) -> ET.Element:
    try:
        tree = ET.parse(path)
    except FileNotFoundError:
        print(
            f"ERROR: no existe {path}. pytest no llegó a escribir el informe JUnit: "
            "el paso murió antes de ejecutar nada (mira su salida más arriba). "
            "Sin informe NO hay evidencia de que el e2e de instalación corriera.\n"
            "Ojo: el workflow BORRA este fichero antes de invocar pytest, "
            "precisamente para que «no existe» signifique «pytest no lo escribió» y "
            "no «lo escribió otra corrida».",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except OSError as exc:
        # FileNotFoundError es subclase de OSError y va ARRIBA: aquí caen los
        # otros modos, que en este job no son teóricos. El informe lo escribe
        # ROOT (`sudo env … pytest`) y lo lee el usuario del runner, así que un
        # umask hostil da EACCES; y un `$JUNIT_REPORT` que apunte a un
        # directorio da EISDIR. Sin esta rama el gate moría con una traza de
        # ElementTree: fallaba cerrado —bien— pero mostrando algo que parece un
        # defecto del gate, que es como un paso se gana el `continue-on-error`.
        print(
            f"ERROR: {path} existe pero no se puede leer ({exc}). El gate no puede "
            "afirmar nada sobre un informe que no abre. Suele ser de permisos: "
            "pytest lo escribe bajo `sudo` y este paso lo lee sin él.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except ET.ParseError as exc:
        print(f"ERROR: {path} no es un XML válido ({exc}).", file=sys.stderr)
        raise SystemExit(1) from None
    return tree.getroot()


def _skip_reason(case: ET.Element) -> str | None:
    """El motivo del ``<skipped>`` de un ``<testcase>``, o None si se ejecutó.

    Un salto de fixture (el gate `E2E_INSTALL`) y uno del cuerpo del test
    producen el MISMO elemento — medido con pytest 9.1.1 sobre un módulo de
    prueba —, así que este gate no puede ni necesita distinguirlos.
    """

    node = case.find("skipped")
    if node is None:
        return None
    return node.get("message") or (node.text or "").strip() or "(sin motivo)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate del e2e de instalación.")
    parser.add_argument("report", type=Path, help="XML de `pytest --junitxml`")
    args = parser.parse_args(argv)

    root = _load(args.report)
    cases = list(root.iter("testcase"))
    by_name = {(c.get("classname", ""), c.get("name", "")): c for c in cases}
    required_keys = {(REQUIRED_MODULE, name) for name in REQUIRED_TESTS}

    saltados = {
        (c.get("classname", ""), c.get("name", "")): reason
        for c in cases
        if (reason := _skip_reason(c)) is not None
    }
    print(
        f"e2e de instalación: {len(cases)} casos en el informe, "
        f"{len(cases) - len(saltados)} ejecutados, {len(saltados)} saltados."
    )
    # Actions mezcla stdout y stderr: sin vaciar el búfer, el resumen saldría
    # DESPUÉS de los problemas y el log se leería al revés.
    sys.stdout.flush()

    problems = False

    # (4) Suelo de descubrimiento: una guarda que no puede fallar no es una guarda.
    if len(cases) < len(REQUIRED_TESTS):
        problems = True
        print(
            f"\nEL INFORME TRAE MENOS CASOS DE LOS EXIGIDOS ({len(cases)} < "
            f"{len(REQUIRED_TESTS)}). O el módulo encogió, o pytest recolectó otra "
            "cosa. En cualquiera de los dos casos este gate estaría pasando en vacío.",
            file=sys.stderr,
        )

    # (2) Los cuatro casos, presentes y NO saltados.
    for name in REQUIRED_TESTS:
        case = by_name.get((REQUIRED_MODULE, name))
        if case is None:
            problems = True
            print(
                f"\nFALTA EN EL INFORME: {REQUIRED_MODULE}::{name}. No se ejecutó, "
                "así que el verde de este job no acredita deploy-1/2/3.",
                file=sys.stderr,
            )
            continue
        reason = saltados.get((REQUIRED_MODULE, name))
        if reason is not None:
            problems = True
            print(
                f"\nSALTADO: {REQUIRED_MODULE}::{name}\n"
                f"  motivo: {reason}\n"
                "  Un salto aquí ES el falso verde que este job existe para cerrar: "
                "el gate `E2E_INSTALL=1` + daemon Docker cae en el SETUP de las "
                "fixtures, así que pytest recolecta, salta y sale 0. Comprueba que "
                "el paso exporta E2E_INSTALL=1 y que el daemon responde.",
                file=sys.stderr,
            )

    # (3) Ningún otro caso saltado. Cero tolerados a propósito (ver el docstring).
    otros = sorted(
        (classname, name, reason)
        for (classname, name), reason in saltados.items()
        if (classname, name) not in required_keys
    )
    if otros:
        problems = True
        print(
            "\nCASOS SALTADOS FUERA DE LA LISTA EXIGIDA. Este gate NO mantiene "
            "lista de tolerados: el workflow invoca sólo "
            "tests/e2e/test_install_from_scratch.py, donde un salto legítimo no "
            "existe. Si la invocación se amplió a propósito, la decisión de qué "
            "salto se tolera se escribe en este script, no se hereda:",
            file=sys.stderr,
        )
        for classname, name, reason in otros:
            print(f"  - {classname}::{name}: {reason}", file=sys.stderr)

    # (5) Fallos y errores: este script no puede ser nunca el que diga «bien»
    #     sobre una suite roja.
    rojos = sorted(
        (c.get("classname", ""), c.get("name", ""), kind)
        for c in cases
        for kind in ("failure", "error")
        if c.find(kind) is not None
    )
    if rojos:
        problems = True
        print("\nCASOS EN ROJO (el e2e SÍ ejecutó, y falló):", file=sys.stderr)
        for classname, name, kind in rojos:
            print(f"  - {classname}::{name} [{kind}]", file=sys.stderr)

    if problems:
        print("\nVeredicto: este job NO acredita que la instalación funcione.", file=sys.stderr)
        return 1

    print(
        f"Veredicto: los {len(REQUIRED_TESTS)} casos de {REQUIRED_MODULE} se "
        "EJECUTARON y pasaron. El stack se instaló, sirvió HTTPS por el proxy, "
        "no publicó 8000/3000 y la credencial revelada autenticó."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
