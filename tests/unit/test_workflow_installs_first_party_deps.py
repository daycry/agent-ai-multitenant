"""Un workflow que instala el api-server instala también sus paquetes hermanos.

El defecto que cierra
---------------------
``eval-on-prompt-change.yml`` instalaba ``packages/shared-llm`` y
``apps/api-server``, y nada más. Funcionó hasta el 2026-08-20, cuando
``api_server.evals.__init__`` pasó a exponer el gate de edición de prompt
(``task_gov_05``): ése lee los presets de aprobación, y con ellos entra
``shared_domain.approval_categories``. El job murió con
``ModuleNotFoundError: No module named 'shared_domain'`` **antes de decidir
nada** (run 32320504352).

Lo que hace peligroso a este fallo no es que rompa, es DÓNDE rompe. Ese job
existe para que un fork sin secretos de proveedor no falle: tiene una rama
``--dry-run`` deliberada. Un `ModuleNotFoundError` en el import se lee, desde
fuera, igual que «el gate de evals está en rojo» — y el arreglo intuitivo es
relajar el gate, que es exactamente lo contrario de lo que hace falta.

Por qué esta forma
------------------
La guarda no intenta resolver el grafo de imports de ``api_server`` (que cambia
cada semana y pediría importar el paquete, con lo que ya no sería una guarda
estática barata). Afirma algo más simple y más estable: **quien instale el
api-server editable instala también los paquetes de primera parte de los que
depende su superficie de import**. Hoy son dos, y los dos están en `ci.yml` con
su comentario.

Si algún día el api-server deja de necesitar uno, esta lista se acorta con el
cambio que lo consiga — y hasta entonces sobra una línea de `pip install`, que
es un precio muy inferior a un job que muere en el import.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2]
_WORKFLOWS = _RAIZ / ".github" / "workflows"

#: Paquetes de primera parte que la superficie de import de `api_server`
#: necesita. `shared-llm` es dependencia de camino declarada (ADR 0021);
#: `shared-domain` entra por el vocabulario canónico (ADR 0048 / Plan 06.18).
_HERMANOS = ("packages/shared-llm", "packages/shared-domain")

_INSTALA_API = re.compile(r'pip install -e\s+"apps/api-server')


def _workflows() -> list[Path]:
    return sorted(_WORKFLOWS.glob("*.yml"))


def test_the_discovery_finds_the_workflows() -> None:
    """No-vacuidad: con cero ficheros, el test de abajo pasa sin comprobar nada."""
    ficheros = _workflows()
    assert len(ficheros) >= 3, f"solo {len(ficheros)} workflows: ¿glob roto?"
    assert any(_INSTALA_API.search(f.read_text(encoding="utf-8")) for f in ficheros), (
        "ningún workflow instala `apps/api-server` editable. O cambió la forma"
        " del comando —y esta guarda dejó de ver lo que cree ver— o CI ya no"
        " instala el api-server, que sería una noticia mayor."
    )


def test_every_workflow_that_installs_the_api_server_installs_its_siblings() -> None:
    faltas: list[str] = []
    for fichero in _workflows():
        texto = fichero.read_text(encoding="utf-8")
        if not _INSTALA_API.search(texto):
            continue
        for hermano in _HERMANOS:
            if f'pip install -e "{hermano}' not in texto:
                faltas.append(f"{fichero.name}: instala apps/api-server pero no {hermano}")

    assert not faltas, (
        "Workflows que instalan el api-server sin sus paquetes de primera parte."
        " El job morirá con `ModuleNotFoundError` en el import, ANTES de correr"
        " nada — y desde fuera eso se lee como «el gate está en rojo», que"
        " invita justo al arreglo contrario.\n  " + "\n  ".join(faltas)
    )
