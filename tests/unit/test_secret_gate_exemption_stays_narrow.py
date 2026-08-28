"""La única exención del gate de secretos no puede crecer.

**Por qué existe este fichero (2026-08-28).** El gate
`scripts/check_no_secret_artifacts.py` busca tokens de Vault por su forma:
`hvs.` seguido de 16+ caracteres de material. La redacción de
`api_server/logging/pii.py:64` usa **el mismo umbral**, así que un test que
compruebe que un token se redacta necesita, por fuerza, un literal con forma de
token — y ese literal dispara el gate. Dos guardas correctas estorbándose.

La salida elegida fue eximir **un literal exacto**, no una ruta. Eximir
`tests/` habría sido lo cómodo y lo peor: ahí es exactamente donde alguien
pegaría un token real mientras depura, y perderíamos la detección donde más
falta hace.

Lo que hace segura esa decisión no es el argumento, es este fichero. Una
exención sin un test que la acote se ensancha sola: alguien añade «y también
este otro», luego «y los de este directorio», y a los seis meses el gate mira
un árbol vacío y pasa en verde. Aquí se afirman las dos mitades:

* el centinela pasa —si no, el arreglo no sirve—, y
* cualquier OTRO token con forma real sigue saltando, **incluso en el mismo
  fichero que ya lleva el centinela**.

Ese último caso es el que de verdad importa: es el fallo por el que una
exención literal se convertiría en una puerta. Si alguien pega un token real
justo debajo del centinela, tiene que saltar igual.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_GATE = _REPO / "scripts" / "check_no_secret_artifacts.py"


def _cargar_gate() -> ModuleType:
    """Importa el script del gate, que vive fuera de un paquete."""
    spec = importlib.util.spec_from_file_location("_gate_secretos", _GATE)
    assert spec and spec.loader, f"no se pudo cargar {_GATE}"
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _token_realista() -> str:
    """Un token con la forma que emite Vault, construido por concatenación.

    Escrito de un tirón, el literal de ESTE fichero dispararía el gate al
    escanearse el árbol — el mismo motivo por el que el propio gate construye
    su patrón por partes.
    """
    return "hvs" + "." + "CAESIJ0kL1mN2oP3qR4sT5uV6wX7yZ8aB9cD0eF1gH2i"


def test_el_centinela_pasa_el_gate() -> None:
    """Si el centinela saltara, la exención no serviría de nada."""
    gate = _cargar_gate()
    texto = f'ROOT_TOKEN = "{gate.FAKE_TOKEN_SENTINEL}"\n'
    limpio = texto.replace(gate.FAKE_TOKEN_SENTINEL, "")
    assert gate.VAULT_TOKEN_RE.search(limpio) is None


def test_el_centinela_tiene_forma_de_token_de_verdad() -> None:
    """Si no la tuviera, no valdría para probar la redacción y sobraría.

    El centinela existe porque `pii.py` sólo redacta con 16+ caracteres de
    material. Un centinela más corto pasaría este gate por la razón equivocada
    —por ser corto, no por estar exento— y dejaría el test de redacción sin
    nada que redactar.
    """
    gate = _cargar_gate()
    assert gate.VAULT_TOKEN_RE.fullmatch(gate.FAKE_TOKEN_SENTINEL), (
        f"el centinela {gate.FAKE_TOKEN_SENTINEL!r} ya no tiene forma de token: "
        "o se acortó, o cambió el patrón. Si es lo segundo, revisa que la "
        "redacción de pii.py siga usando el mismo umbral."
    )


def test_otro_token_sigue_saltando() -> None:
    """La exención es UN literal, no una familia."""
    gate = _cargar_gate()
    texto = f'ROOT_TOKEN = "{_token_realista()}"\n'
    limpio = texto.replace(gate.FAKE_TOKEN_SENTINEL, "")
    assert gate.VAULT_TOKEN_RE.search(limpio) is not None, (
        "un token con forma real ha dejado de saltar: la exención se ha "
        "ensanchado o el patrón se ha debilitado"
    )


def test_un_token_real_junto_al_centinela_sigue_saltando() -> None:
    """El caso que convierte una exención literal en una puerta abierta.

    Alguien pega un token de verdad en el mismo fichero donde ya vive el
    centinela — depurando, o copiando de una sesión. Si el gate se limitase a
    «este fichero está exento», no lo vería. Por eso la exención retira el
    centinela del texto ANTES de buscar, en vez de perdonar el fichero entero.
    """
    gate = _cargar_gate()
    texto = (
        f'FAKE = "{gate.FAKE_TOKEN_SENTINEL}"\n'
        f'DE_VERDAD = "{_token_realista()}"   # <- esto tiene que saltar\n'
    )
    limpio = texto.replace(gate.FAKE_TOKEN_SENTINEL, "")
    assert gate.VAULT_TOKEN_RE.search(limpio) is not None, (
        "un token real ha pasado por convivir con el centinela: la exención "
        "está perdonando el fichero en vez del literal"
    )


def test_ningun_fichero_rastreado_lleva_un_token_que_no_sea_el_centinela() -> None:
    """El aviso local del fallo que ya costó dos vueltas de CI (2026-08-28).

    El gate de secretos corre **sólo en CI**, y a propósito: los artefactos que
    persigue están en `.gitignore`, así que un gate que mirase lo staged no
    vería jamás el problema que lo motivó (lo dice su propio `--help`). Correcto
    para su pregunta, pero deja sin red la otra: un literal con forma de token
    dentro de un fichero **rastreado**.

    Eso pasó dos veces seguidas el mismo día. Alguien escribe un test de Vault,
    inventa un token falso plausible, y el fallo aparece un cuarto de hora
    después en CI, en un job que no habla de secretos. Cada vuelta cuesta un
    push, un run entero y volver a mirar logs.

    Esta comprobación no sustituye al gate: hace la pregunta pequeña —la que sí
    se puede contestar en local— y la contesta en milisegundos.
    """
    gate = _cargar_gate()
    rastreados = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    culpables: list[str] = []
    for rel in rastreados:
        ruta = _REPO / rel
        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - fichero recién borrado
            continue
        if gate.VAULT_TOKEN_RE.search(texto.replace(gate.FAKE_TOKEN_SENTINEL, "")):
            culpables.append(rel)

    assert not culpables, (
        f"estos ficheros llevan un literal con forma de token de Vault: {culpables}.\n"
        "Si es un fixture, usa el centinela registrado en "
        "`scripts/check_no_secret_artifacts.py` (FAKE_TOKEN_SENTINEL) en vez de "
        "inventar uno: es el único exento, y usarlo evita que este defecto "
        "aparezca quince minutos más tarde en un job de CI que no habla de "
        "secretos.\n"
        "Si NO es un fixture, es un secreto de verdad: revócalo antes de nada."
    )


def test_la_exencion_sigue_siendo_una_sola() -> None:
    """Un contador, para que ensanchar la exención sea un acto deliberado.

    No impide añadir un segundo centinela: obliga a que quien lo haga toque
    este test y explique por qué. Es la diferencia entre una excepción y una
    costumbre.
    """
    gate = _cargar_gate()
    exenciones = [nombre for nombre in dir(gate) if nombre.isupper() and "SENTINEL" in nombre]
    assert exenciones == ["FAKE_TOKEN_SENTINEL"], (
        f"el gate de secretos declara ahora {exenciones}. Cada centinela nuevo "
        "es una puerta más: si hace falta, escribe aquí por qué y qué la acota."
    )
