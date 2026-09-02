"""La familia `file`: read / write / delete / move / list (task_02_16).

Every path is resolved relative to the workspace root and must stay
inside it — an absolute path or a `../` traversal that escapes the
workspace is rejected before any filesystem access. The agent only ever
sees /workspace.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.tools import ToolResult

# Cap on a single file_read so a huge file cannot blow up the steps_log.
_MAX_READ_BYTES = 1_000_000

# The Claude Code CLI drops its own state (.claude.json ~25KB, .claude/) into the
# working dir. Hide it from listings so the agent never wastes a turn reading CLI
# config into its context — it is not part of the task's workspace.
_CLI_ARTIFACTS = frozenset({".claude", ".claude.json"})

#: Filtro de `file_list` cuando no llega ninguno. **No es el `"**/*"` que el
#: esquema anunciaba**: ver :meth:`WorkspaceFiles.file_list`, punto 2.
_DEFAULT_LIST_PATTERN = "*"

#: Tope de entradas que `file_list` devuelve en una llamada.
#:
#: Medido el 2026-09-01 sobre las 893 salidas reales de `list_files` del
#: `steps_log`: mediana 1 entrada, p95 16, **máximo 54**. 150 deja pasar intacto
#: el 100% de lo observado con 2,8x de margen sobre el peor caso real.
#:
#: **El coste se mide en el PROMPT, no en el JSON**, y esa distinción costó una
#: primera versión de esta constante. Dos correcciones sobre la medición ingenua:
#:
#:   * ~74 bytes por entrada, no ~60. Los 60 salían de las salidas VIEJAS, donde
#:     `name` era un nombre suelto (`docs`); ahora es la ruta relativa, y en un
#:     `vendor/` profundo llega a 108 B.
#:   * el observation se renderiza **dos veces** en su propio turno (la cola
#:     `context[-8:]` más `Last observation`) y sigue en esa cola ocho turnos más,
#:     refacturándose en cada uno.
#:
#: Con 500 y el árbol real del incidente (11.956 entradas), un `"**/*"` costaba
#: ~22.000 tokens en su turno y ~104.000 acumulados con la cola — el presupuesto
#: ENTERO del run (`Budgets.max_tokens` = 100.000). Con 150 el peor caso queda en
#: ~11 KB / ~6.600 tokens, que es el orden de un `file_read` mediano.
#:
#: El número que hay al otro lado es el que obliga a poner tope: la rama del plan
#: del incidente llegó a **10.318 ficheros**. Un `"**/*"` sobre la raíz los
#: devolvería todos.
#:
#: Si cambias este número, cambia también la descripción del catálogo
#: (`api_server.seeds.builtin_tools`): es lo único que el modelo lee, y
#: `test_el_tope_anunciado_es_el_que_se_aplica` falla si divergen.
_MAX_LIST_ENTRIES = 150


#: El enlace del worktree con su rama. Ver :meth:`_mutable_path`.
_GIT_DIR = ".git"


def _rutas_versionadas(crudo: Iterable[str] | str) -> set[str]:
    """Normaliza lo que llega del contrato ``AGENT_TRACKED_PATHS``.

    El worker publica los DIRECTORIOS versionados en la rama, a cualquier
    profundidad, como rutas relativas POSIX separadas por saltos de línea
    (``"app\\napp/Config\\napp/Controllers\\nsystem"``). Aquí se limpia ese bloque
    antes de compararlo con nada, porque una comparación literal contra texto de
    un env es la forma barata de que la guarda **pase en vacío**: ``"app/"`` no
    casaría con ``"app"``, el test seguiría verde y el borrado también ocurriría.

    Dos detalles que parecen paranoia y no lo son:

    * un ``str`` también es iterable, y recorrerlo daría el conjunto
      ``{'a', 'p', '\\n', …}``, que no casa con ninguna ruta. Se acepta el
      bloque crudo y se parte por líneas;
    * un worker anterior al contrato actual publicaba sólo el primer nivel.
      Ese bloque sigue valiendo tal cual: protege menos profundidad, no menos.

    Devuelve un ``set`` mutable a propósito: la protección SIGUE AL CONTENIDO
    cuando :meth:`WorkspaceFiles.file_move` traslada un directorio versionado
    (:meth:`WorkspaceFiles._trasladar_proteccion`).
    """
    lineas = crudo.splitlines() if isinstance(crudo, str) else crudo
    limpias: set[str] = set()
    for entrada in lineas:
        ruta = entrada.strip().replace("\\", "/").strip("/")
        while ruta.startswith("./"):
            ruta = ruta[2:]
        if not ruta or ruta in {".", ".."} or any(p in {".", ".."} for p in ruta.split("/")):
            continue
        limpias.add(ruta)
    return limpias


#: Las ÚNICAS grafías de texto que valen como booleano. Ver :func:`_bandera`.
_BANDERAS_TEXTO = {"true": True, "false": False, "1": True, "0": False}


def _bandera(nombre: str, crudo: object) -> bool | ToolResult:
    """Coerciona la bandera DESTRUCTIVA de una tool, o devuelve un error accionable.

    Compartida por ``delete_file.recursive`` y ``move_file.overwrite`` — una
    sola, no una copia en cada tool: la misma regla escrita dos veces envejece a
    media velocidad, y el día que se corrija en un sitio la otra mitad queda
    aceptando lo que aquí se rechaza.

    **El defecto que cierra, verificado el 2026-08-31.** Las dos tools hacían
    ``bool(args.get("overwrite", False))``, y en Python la CADENA ``"false"`` es
    truthy — igual que ``"no"``. Medido: el destino se reemplazaba. El modelo del
    incidente es ``gpt-oss:120b`` vía ollama, que emite los booleanos como cadena
    a menudo. Todo el argumento de diseño de estas dos tools es que «la variante
    destructiva se pide A PROPÓSITO»; con esa coerción, el que decía «no»
    obtenía «sí», y era además la puerta por la que se alcanzaba sin intención el
    destrozo que impide :meth:`WorkspaceFiles._rechazo_de_destino`.

    **Por qué un valor raro es un ERROR y no un «no» silencioso.** Degradar a
    «sí» no se discute: es el defecto. Pero degradar a «no» tampoco es gratis —
    el agente creería haber pedido la variante destructiva y recibiría un «pasa
    ``overwrite=true``» que, desde su punto de vista, ya había pasado. Ese bucle
    tiene precio medido: en la ejecución ``01a05881-89d7-79fa-be72-bd0e7c1a9fbb``
    catorce llamadas y el 60 % del presupuesto se fueron contra un requisito de
    FORMA que el error no explicaba. Un error que dice qué valor se esperaba
    convierte ese bucle en una llamada corregida.

    **Y no se aceptan sinónimos** (``"yes"``/``"no"``/``"on"``/``"off"``) por la
    misma razón por la que ``move_file`` no acepta ``force`` ni ``replace`` como
    sinónimos de la CLAVE ``overwrite``: un contrato estricto cuyo vocabulario
    crece deja de ser un contrato, y lo que el esquema del catálogo anuncia es un
    ``boolean``. Se aceptan el booleano de verdad y las dos grafías que un
    serializador JSON produce sin ambigüedad (``"true"``/``"false"``, ``1``/``0``).

    Ausente —clave que no viene, o ``null``— sigue significando ``False``, que es
    el default no destructivo de las dos tools.
    """
    if crudo is None:
        return False
    if isinstance(crudo, bool):
        return crudo
    if isinstance(crudo, str):
        valor = _BANDERAS_TEXTO.get(crudo.strip().lower())
        if valor is not None:
            return valor
    elif isinstance(crudo, int) and crudo in (0, 1):
        return bool(crudo)
    return ToolResult(
        ok=False,
        error=(
            f"'{nombre}' must be a boolean: true or false. Got {crudo!r}. "
            'The strings "true"/"false" and the numbers 1/0 are accepted too; '
            "anything else is refused instead of guessed, because guessing "
            f"'{nombre}' wrong either destroys work or silently withholds the "
            "operation you asked for."
        ),
    )


class _PatronInvalidoError(ValueError):
    """El `pattern` no se puede interpretar. El mensaje viaja tal cual al modelo.

    Es una excepción y no un `[]` a propósito. Devolver «cero coincidencias»
    sobre un patrón que no se llegó a entender es indistinguible, desde el lado
    del agente, de «ese fichero no existe» — y eso es EXACTAMENTE el defecto que
    esta familia de arreglos persigue, sólo que una capa más abajo.
    """


def _mensaje_llaves(patron: str, faltan: int) -> str:
    if faltan > 0:
        sugerido = patron + "}" * faltan
        return (
            f"unbalanced braces in pattern {patron!r}: every '{{' needs its '}}'. "
            f"Braces list alternatives, so you probably meant {sugerido!r}."
        )
    return (
        f"unbalanced braces in pattern {patron!r}: a '}}' with no '{{' before it. "
        "Braces list alternatives, e.g. 'composer.{json,lock}'."
    )


def _grupo_de_llaves(patron: str) -> tuple[int, int] | None:
    """Los índices del primer grupo ``{...}`` de primer nivel, o ``None``.

    Se salta el interior de una clase ``[...]`` porque ahí una llave es un
    carácter más. Levanta :class:`_PatronInvalidoError` si no equilibran.
    """
    inicio = -1
    nivel = 0
    en_clase = False
    for i, c in enumerate(patron):
        if en_clase:
            en_clase = c != "]"
        elif c == "[":
            en_clase = True
        elif c == "{":
            if nivel == 0:
                inicio = i
            nivel += 1
        elif c == "}":
            if nivel == 0:
                raise _PatronInvalidoError(_mensaje_llaves(patron, 0))
            nivel -= 1
            if nivel == 0:
                return inicio, i
    if nivel:
        raise _PatronInvalidoError(_mensaje_llaves(patron, nivel))
    return None


def _partir_alternativas(cuerpo: str) -> list[str]:
    """Parte por las comas de PRIMER nivel: ``a,{b,c}`` da ``['a', '{b,c}']``."""
    partes: list[str] = []
    actual: list[str] = []
    nivel = 0
    en_clase = False
    for c in cuerpo:
        if en_clase:
            en_clase = c != "]"
        elif c == "[":
            en_clase = True
        elif c == "{":
            nivel += 1
        elif c == "}":
            nivel -= 1
        elif c == "," and nivel == 0:
            partes.append("".join(actual))
            actual = []
            continue
        actual.append(c)
    partes.append("".join(actual))
    return partes


def _expandir_llaves(patron: str) -> list[str]:
    """``a.{json,lock}`` -> ``['a.json', 'a.lock']``.

    `pathlib` NO entiende las llaves, y el modelo las manda: 39 de las 965
    llamadas a `list_files` medidas el 2026-09-01 las llevan
    (``**/composer.{json,lock}``, ``{app,tests}/**/*.php``). Sin expandirlas, un
    patrón perfectamente razonable devolvería vacío sobre un workspace que sí
    contiene los ficheros.
    """
    grupo = _grupo_de_llaves(patron)
    if grupo is None:
        return [patron]
    inicio, fin = grupo
    prefijo, resto = patron[:inicio], patron[fin + 1 :]
    salida: list[str] = []
    for alternativa in _partir_alternativas(patron[inicio + 1 : fin]):
        salida.extend(_expandir_llaves(f"{prefijo}{alternativa}{resto}"))
    return salida


def _fin_de_clase(patron: str, inicio: int) -> int:
    """El índice del ``]`` que cierra la clase abierta en `inicio`."""
    i = inicio + 1
    if i < len(patron) and patron[i] in "!^":
        i += 1
    if i < len(patron) and patron[i] == "]":  # `[]]` = la clase del literal ']'
        i += 1
    while i < len(patron) and patron[i] != "]":
        i += 1
    if i >= len(patron):
        raise _PatronInvalidoError(
            f"unterminated '[' in pattern {patron!r}: brackets open a character "
            "class and have to be closed, e.g. '[abc]*.php'."
        )
    return i


def _clase_a_regex(cuerpo: str, patron: str) -> str:
    negada = cuerpo[:1] in ("!", "^")
    if negada:
        cuerpo = cuerpo[1:]
    if not cuerpo:
        raise _PatronInvalidoError(
            f"empty character class in pattern {patron!r}: '[]' matches nothing, e.g. '[abc]*.php'."
        )
    # `-` se deja crudo (los rangos `a-z` tienen que seguir funcionando); el
    # resto de metacaracteres de una clase de regex se neutraliza.
    cuerpo = cuerpo.replace("\\", "\\\\").replace("]", "\\]").replace("^", "\\^")
    return f"[^/{cuerpo}]" if negada else f"[{cuerpo}]"


def _a_regex(patron: str) -> re.Pattern[str]:
    """Traduce UN glob (ya sin llaves) a la regex que casa una ruta relativa.

    La traducción se escribe a mano en vez de delegar en `Path.glob` por dos
    razones que no son de estilo:

    * `Path.glob` cambió de semántica entre las versiones que conviven aquí —en
      3.12 un ``**`` suelto casa sólo directorios y en 3.13 también ficheros—, y
      el runtime va sobre `python:3.12-slim` mientras los tests corren en 3.13.
      Un contrato que depende de eso no es un contrato;
    * hace falta el recorrido propio de todas formas (podar `_CLI_ARTIFACTS`,
      contar el total sin materializarlo, acotar la profundidad), y tener el
      matcher aparte permite fijarlo con tests que no tocan el disco.
    """
    piezas: list[str] = []
    i, n = 0, len(patron)
    while i < n:
        c = patron[i]
        if c == "*":
            j = i
            while j < n and patron[j] == "*":
                j += 1
            if j - i >= 2 and j < n and patron[j] == "/":
                # `**/` = cero o más segmentos completos. El «cero» es lo que
                # hace que `**/*.php` encuentre también los de la raíz.
                piezas.append("(?:[^/]+/)*")
                i = j + 1
            elif j - i >= 2:
                piezas.append(".*")
                i = j
            else:
                piezas.append("[^/]*")
                i = j
        elif c == "?":
            piezas.append("[^/]")
            i += 1
        elif c == "[":
            fin = _fin_de_clase(patron, i)
            piezas.append(_clase_a_regex(patron[i + 1 : fin], patron))
            i = fin + 1
        else:
            piezas.append(re.escape(c))
            i += 1
    return re.compile("".join(piezas) + r"\Z")


def _prefijo_literal(patron: str) -> tuple[str, ...]:
    """Los segmentos SIN comodín con los que empieza el patrón.

    ``app/**/*.php`` -> ``('app',)``; ``**/*.php`` -> ``()``. Es lo que permite
    no bajar a donde no puede haber nada, y es exacto: esos segmentos se
    traducen a texto literal al principio de la regex, así que una ruta que no
    los lleve no puede casar por debajo por mucho que se baje.
    """
    literales: list[str] = []
    for segmento in patron.split("/"):
        if any(c in segmento for c in "*?["):
            break
        literales.append(segmento)
    return tuple(literales)


@dataclass(frozen=True)
class _Glob:
    """Un `pattern` compilado: cómo casa y hasta dónde hay que bajar a buscarlo."""

    patron: str
    alternativas: tuple[re.Pattern[str], ...]
    #: Niveles a recorrer, 1 = sólo el directorio pedido. ``None`` = sin límite.
    profundidad: int | None
    recursivo: bool
    #: Un prefijo literal por alternativa. Ver :meth:`puede_haber_algo_bajo`.
    prefijos: tuple[tuple[str, ...], ...]

    def casa(self, relativa: str) -> bool:
        return any(rx.match(relativa) for rx in self.alternativas)

    def puede_haber_algo_bajo(self, segmentos: tuple[str, ...]) -> bool:
        """¿Vale la pena abrir este directorio, o no puede casar nada dentro?

        Medido el 2026-09-01 sobre un árbol de 10.400 entradas con la forma del
        incidente: sin esta poda, ``app/**/*.php`` —justo el patrón que la nota
        de truncado recomienda para acotar— recorría el árbol ENTERO, 176 ms
        para devolver una coincidencia. Recomendar al modelo que acote y que
        acotar no le salga más barato es recomendarle humo.

        No cambia ningún resultado: si el segmento ``i`` del directorio difiere
        del segmento ``i`` del prefijo literal, ninguna ruta por debajo puede
        casar, porque ese segmento aparece como texto literal en la regex.
        """
        for prefijo in self.prefijos:
            comunes = min(len(segmentos), len(prefijo))
            if all(segmentos[i] == prefijo[i] for i in range(comunes)):
                return True
        return False


def _compilar_glob(patron: str) -> _Glob:
    """Compila el `pattern` del contrato, o levanta :class:`_PatronInvalidoError`.

    La profundidad sale del propio patrón y es la pieza que hace que el default
    siga costando lo mismo que antes: sin ``**``, un patrón de ``k`` segmentos
    no puede casar nada por debajo del nivel ``k``, así que el recorrido para
    ahí. ``*`` (el default, 279 llamadas reales) recorre un solo `scandir`,
    exactamente como el `iterdir()` que había.
    """
    alternativas = _expandir_llaves(patron)
    for alternativa in alternativas:
        if alternativa.startswith("/") or ".." in alternativa.split("/"):
            raise _PatronInvalidoError(
                f"'pattern' must be relative to 'path' and stay inside the "
                f"workspace: {patron!r} uses a leading '/' or a '..'. Point "
                f"'path' at the directory you mean and give a relative glob, "
                f"e.g. 'app/**/*.php'."
            )
    recursivo = any("**" in alternativa for alternativa in alternativas)
    profundidad = None if recursivo else max(a.count("/") for a in alternativas) + 1
    return _Glob(
        patron=patron,
        alternativas=tuple(_a_regex(a) for a in alternativas),
        profundidad=profundidad,
        recursivo=recursivo,
        prefijos=tuple(_prefijo_literal(a) for a in alternativas),
    )


def _entrada(relativa: str, ruta: Path) -> dict[str, object]:
    """Una entrada del listado. `name` es la RUTA relativa al `path` pedido.

    En el listado plano coincide con el nombre —así que el contrato de antes se
    mantiene—, pero con un patrón recursivo el nombre suelto no sirve: cuarenta
    ``Home.php`` indistinguibles no le dicen al agente cuál abrir.
    """
    try:
        return {
            "name": relativa,
            "type": "dir" if ruta.is_dir() else "file",
            "size": ruta.stat().st_size if ruta.is_file() else None,
        }
    except OSError:
        # El árbol se movió entre el recorrido y el `stat` (otro contenedor, un
        # enlace roto). Se informa de la entrada sin inventarle metadatos, que
        # es mejor que perderla del listado.
        return {"name": relativa, "type": "file", "size": None}


def _nada_casa(mostrada: object, patron: str, glob: _Glob, escaneadas: int) -> str:
    """La nota del cero coincidencias: el caso que hizo repetir ocho veces.

    Un ``[]`` a secas es indistinguible de «ese fichero no existe». Con el
    número de entradas recorridas y —si el patrón no era recursivo— su forma
    recursiva ya escrita, la respuesta que antes no decía nada pasa a decir el
    siguiente paso.
    """
    if escaneadas == 0:
        return f"'{mostrada}' is empty: there are no entries under it at all."
    if not glob.recursivo:
        return (
            f"0 of the {escaneadas} entries visited under '{mostrada}' match "
            f"'{patron}'. This pattern is NOT recursive - only '**' descends into "
            f"subdirectories. Try '**/{patron}' to search the whole tree."
        )
    # Se dice «visited» y no «all the entries under it» porque el recorrido poda
    # lo que el prefijo literal del patrón no puede alcanzar
    # (:meth:`_Glob.puede_haber_algo_bajo`): con `app/**/*.rs` no se baja a
    # `vendor/`, así que prometer que se miró el árbol entero sería falso. Un
    # número que suena a total y no lo es es la misma clase de mentira barata
    # que este arreglo persigue.
    return (
        f"0 of the {escaneadas} entries visited under '{mostrada}' match this "
        f"pattern - widen the filter or check the path."
    )


def _recorrer(raiz: Path, glob: _Glob) -> tuple[list[tuple[str, Path]], int, int]:
    """Recorre bajo `raiz` y devuelve `(coincidencias, escaneadas, ilegibles)`.

    Tres decisiones que no son de estilo:

    * **se cuenta todo lo que casa, se materializa sólo lo que se devuelve.**
      El tope de :data:`_MAX_LIST_ENTRIES` sólo puede anunciarse honestamente
      («había N») si se sabe cuántas había, y eso exige terminar el recorrido.
      Terminarlo es barato —`scandir` no lee contenido— mientras que hacer
      `stat` de 10.318 entradas para tirar 9.818 no lo es: el `stat` se hace
      después, sobre las que se devuelven;
    * **la profundidad la manda el patrón** (:func:`_compilar_glob`). El default
      `*` recorre un único `scandir`, igual que el `iterdir()` de antes: el
      arreglo no encarece el caso normal;
    * **no se sigue un enlace simbólico a directorio.** Se informa de él como
      entrada, pero no se desciende: un enlace a un ancestro es un recorrido
      infinito, y un enlace fuera del workspace sería una fuga por la puerta de
      atrás de la jaula de :meth:`WorkspaceFiles._safe_path`.

    Un directorio ilegible se cuenta en vez de reventar la llamada —el caso
    medido en :meth:`WorkspaceFiles._delete_tree`, un subárbol dejado por otro
    contenedor sin permisos— y ese número acaba en la nota del resultado. Un
    subárbol saltado en silencio sería el mismo defecto de este arreglo con otra
    cara.
    """
    coincidencias: list[tuple[str, Path]] = []
    escaneadas = 0
    ilegibles = 0
    pendientes: list[tuple[Path, tuple[str, ...], int]] = [(raiz, (), 1)]
    while pendientes:
        directorio, segmentos, nivel = pendientes.pop()
        try:
            with os.scandir(directorio) as hijos:
                entradas = list(hijos)
        except OSError:
            ilegibles += 1
            continue
        prefijo = "".join(f"{s}/" for s in segmentos)
        for hijo in entradas:
            if hijo.name in _CLI_ARTIFACTS:
                # Se poda el subárbol entero, no sólo la entrada: con un patrón
                # recursivo, ocultar `.claude` y bajar dentro devolvería su
                # contenido y el filtro no habría servido de nada.
                continue
            escaneadas += 1
            relativa = f"{prefijo}{hijo.name}"
            if glob.casa(relativa):
                coincidencias.append((relativa, Path(hijo.path)))
            if glob.profundidad is not None and nivel >= glob.profundidad:
                continue
            hijos_segmentos = (*segmentos, hijo.name)
            if not glob.puede_haber_algo_bajo(hijos_segmentos):
                continue
            try:
                bajar = hijo.is_dir() and not hijo.is_symlink()
            except OSError:
                continue
            if bajar:
                pendientes.append((Path(hijo.path), hijos_segmentos, nivel + 1))
    coincidencias.sort(key=lambda par: par[0])
    return coincidencias, escaneadas, ilegibles


def _relativa(ruta: Path, root: Path) -> str:
    """La ruta tal como la ve el agente: relativa al workspace y en POSIX.

    El agente sólo ve ``/workspace`` (lo dice el docstring del módulo), así que
    una ruta absoluta del host no le sirve para NADA: no puede pasarla a la
    siguiente llamada ni reconocerla, y de paso le enseña la disposición del
    host. Todo mensaje que salga de este módulo habla en estas rutas.
    """
    try:
        return ruta.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - la jaula de ruta ya lo garantiza
        return "<outside the workspace>"


def _codigo_estable(exc: OSError) -> str:
    """El identificador del fallo que NO cambia con el idioma del host.

    ``str(exc)`` en un Windows en castellano da «[WinError 183] No se puede
    crear un archivo que ya existe: 'C:\\…'». Eso viaja al modelo, que es quien
    tiene que decidir el siguiente paso: un texto que cambia de idioma según
    dónde corra el worker no lo puede reconocer ni él ni un test. El ``errno``
    sí es estable y está en inglés (``EEXIST``, ``ENOTEMPTY``, ``EACCES``), y
    dice exactamente lo mismo. Cuando no hay ``errno`` —``shutil.Error`` no lo
    trae— queda el nombre cualificado de la excepción, que también es estable.
    """
    if exc.errno:
        nombre = errno.errorcode.get(exc.errno)
        if nombre:
            return nombre
    tipo = type(exc)
    if tipo.__module__ == "builtins":
        return tipo.__name__
    return f"{tipo.__module__}.{tipo.__name__}"


def _error_de_so(exc: OSError, *, operacion: str) -> ToolResult:
    """Un fallo del sistema, contado en el vocabulario de este módulo.

    El contexto (qué se intentaba, sobre qué rutas relativas) lo pone quien
    llama, que lo sabe mejor que la excepción; de la excepción se toma sólo el
    código estable. Ver :func:`_codigo_estable` y :func:`_relativa`.
    """
    return ToolResult(ok=False, error=f"{operacion} [{_codigo_estable(exc)}]")


#: Prefijo ÚNICO de todo lo que estas tools dejan a un lado del sitio real.
#:
#: **Por qué hay un hermano transitorio, y por qué el patrón es UNO.** Las tres
#: tools que mutan el workspace —``write_file``, ``delete_file`` con ``recursive``
#: y ``move_file`` con ``overwrite``— no pueden destruir primero y fallar después
#: (el argumento entero está en :meth:`WorkspaceFiles._ejecutar_movimiento`), así
#: que las tres trabajan contra un hermano en el MISMO directorio: el destino
#: apartado antes de pisarlo, el árbol apartado antes de borrarlo, el contenido
#: nuevo escrito al lado antes de reemplazar. En el camino feliz ese hermano
#: desaparece; cuando el descarte final no se puede (en Windows, ficheros de
#: sólo lectura o abiertos por otro proceso) se queda.
#:
#: **Y ese residuo tiene que quedar FUERA del commit.** El cierre de tarea hace
#: ``git add -A`` (``workers.plan_git.commit_task``), así que un
#: hermano superviviente entra en el commit del plan como si fuera deliverable.
#: El prefijo es literal y va DELANTE justamente para que una sola línea lo
#: excluya entero —``.agent-runtime-tmp.*``— en vez de un glob con comodín por
#: los dos lados, que además podría tapar ficheros reales. Dónde vive esa línea
#: lo decide el worker (el ``.gitignore`` que siembra en el worktree, o un
#: pathspec de exclusión en ese mismo ``git add``); aquí se fija el patrón y se
#: deja escrito que **sin esa línea el residuo se commitea**.
#:
#: Que el patrón se acuerde aquí y se aplique allí es a propósito: ``apps/`` no
#: es este paquete, y un nombre inventado dos veces en dos sitios es exactamente
#: como envejece mal. Un solo patrón para las tres tools significa además una
#: sola línea que mantener, no una por tool que alguien olvide al añadir la
#: cuarta.
_PREFIJO_TRANSITORIO = ".agent-runtime-tmp."


def _nombre_libre(objetivo: Path) -> Path:
    """Un nombre LIBRE al lado de `objetivo`, con :data:`_PREFIJO_TRANSITORIO`.

    No crea nada: sólo elige. Quien llama decide si va a renombrar el original a
    ese nombre (:func:`_apartar`) o a escribir ahí el contenido nuevo
    (:meth:`WorkspaceFiles._escribir_atomico`).

    Se busca uno libre en vez de reutilizar siempre el primero porque un residuo
    previo —el de un descarte que no pudo— puede contener datos recuperables, y
    pisarlo destruiría justo lo que el prefijo existe para conservar.
    """
    for intento in range(1000):
        candidato = objetivo.with_name(f"{_PREFIJO_TRANSITORIO}{objetivo.name}.{intento}")
        if not candidato.exists():
            return candidato
    raise OSError(  # pragma: no cover - exige 1000 restos previos en el mismo directorio
        errno.EEXIST, "no free scratch name left beside it", str(objetivo)
    )


def _dar_permiso_de_escritura(ruta: str) -> None:
    """``chmod`` al elemento y a su padre, ignorando lo que no se pueda."""
    escribible = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
    for objetivo in (os.path.dirname(ruta) or ".", ruta):
        try:
            os.chmod(objetivo, escribible)
        except OSError:
            continue


def _rmtree_forzado(ruta: Path) -> None:
    """``shutil.rmtree`` que da permiso de escritura y reintenta lo que falló.

    Cubre los dos motivos habituales por los que un descarte no puede: en Windows
    un fichero de sólo lectura no se desenlaza; en POSIX un directorio sin ``w``
    no deja desenlazar lo que contiene. Lo que siga sin poder borrarse levanta
    la excepción original y quien llama decide.
    """

    def _reintentar(func: object, path: str, _exc: BaseException) -> None:
        _dar_permiso_de_escritura(path)
        func(path)  # type: ignore[operator]

    shutil.rmtree(ruta, onexc=_reintentar)


def _desenlazar_forzado(ruta: Path) -> None:
    try:
        ruta.unlink()
    except PermissionError:
        _dar_permiso_de_escritura(str(ruta))
        ruta.unlink()


def _apartar(objetivo: Path) -> Path:
    """Retira `objetivo` a un nombre libre A SU LADO y devuelve dónde quedó.

    Un renombrado en el mismo directorio, que en el mismo sistema de ficheros es
    atómico y no copia nada: el árbol sigue entero, sólo con otro nombre, hasta
    que quien llama confirme que la operación salió bien. Ése es el punto entero
    del cambio de orden — ver :meth:`WorkspaceFiles._ejecutar_movimiento`.

    Y hay una segunda propiedad que es la que salva a :meth:`WorkspaceFiles._delete_tree`:
    renombrar pide permiso sobre el **directorio padre**, no sobre el contenido.
    Por eso puede apartar entero un árbol que ``shutil.rmtree`` no consigue
    desmontar — el caso medido, un subárbol sin permiso de escritura dejado por
    otro contenedor.
    """
    candidato = _nombre_libre(objetivo)
    objetivo.rename(candidato)
    return candidato


@dataclass
class WorkspaceFiles:
    """File tools confined to one workspace directory."""

    root: str = "/workspace"

    #: Directorios versionados en la rama del plan, a cualquier profundidad, tal
    #: como los publica el worker. **Vacía o ausente ⇒ sin la protección de
    #: :meth:`_delete_tree`**: un stack a medio desplegar (worker anterior al
    #: contrato, imagen nueva) se comporta como antes en vez de rechazar
    #: borrados legítimos que nadie sabría explicar.
    tracked_paths: Iterable[str] | str = ()

    _rastreadas: set[str] = field(init=False, repr=False, default_factory=set)

    def __post_init__(self) -> None:
        self._rastreadas = _rutas_versionadas(self.tracked_paths)

    def _safe_path(self, raw: object) -> Path | ToolResult:
        """Resolve `raw` under the workspace root, or a failed ToolResult.

        An absolute path or a traversal escaping the root is rejected —
        `Path(root) / raw` followed by `resolve()` collapses any `..`,
        and the result must still sit under (or be) the root.
        """
        if not isinstance(raw, str) or not raw.strip():
            return ToolResult(ok=False, error="a non-empty 'path' is required")
        root = Path(self.root).resolve()
        candidate = (root / raw).resolve()
        if candidate != root and root not in candidate.parents:
            return ToolResult(ok=False, error=f"path escapes the workspace: {raw}")
        return candidate

    def _mutable_path(self, raw: object) -> Path | ToolResult:
        """Como :meth:`_safe_path`, pero además prohíbe tocar ``.git``.

        Medido en vivo el 2026-08-31: un agente que intentaba
        ``composer create-project codeigniter4/framework .`` —que exige
        directorio vacío— borró ``.git`` para quitarlo de en medio. Instaló el
        framework correctamente y `php spark routes` respondió, pero al cerrar la
        tarea ``git add -A`` salió con «fatal: not a git repository» y el
        deliverable se perdió: hecho y no entregable.

        Desde el lado del agente eso NO es un error. Es un fichero que estorba, y
        nada le dice que sostiene los principios 4 y 5 del sistema (worktree por
        tarea; plan = rama con trailers). La guarda tiene que estar aquí, no en
        el prompt: un prompt se puede ignorar bajo presión de una herramienta que
        insiste en un directorio vacío.

        Por qué la protección que había no lo cubría, que es lo que hay que
        recordar: ``file_delete`` ya rechaza directorios «so a stray path cannot
        wipe a subtree», y en un clon normal eso basta porque ``.git`` ES un
        directorio. En un WORKTREE es un FICHERO con un puntero ``gitdir:``, así
        que la guarda dejaba de aplicar justo donde vive el modelo del sistema.
        """
        resolved = self._safe_path(raw)
        if isinstance(resolved, ToolResult):
            return resolved
        root = Path(self.root).resolve()
        try:
            partes = resolved.relative_to(root).parts
        except ValueError:  # pragma: no cover - _safe_path ya lo garantiza
            return ToolResult(ok=False, error=f"path escapes the workspace: {raw}")
        if _GIT_DIR in partes:
            return ToolResult(
                ok=False,
                error=(
                    "refusing to touch '.git': it links this worktree to the "
                    "plan branch, and without it your work cannot be committed "
                    "or pushed. If a command demands an empty directory, run it "
                    "in a subdirectory and move the result, or use its "
                    "--no-install / existing-directory mode."
                ),
            )
        return resolved

    def file_read(self, args: dict[str, object]) -> ToolResult:
        resolved = self._safe_path(args.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.is_file():
            return ToolResult(ok=False, error=f"not a file: {args.get('path')}")
        if resolved.stat().st_size > _MAX_READ_BYTES:
            return ToolResult(ok=False, error=f"file exceeds {_MAX_READ_BYTES} bytes")
        relativa = _relativa(resolved, Path(self.root).resolve())
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # No es un fallo del SO, así que no tiene errno: se cuenta aparte y
            # sin el volcado de bytes de la excepción, que al modelo no le dice
            # nada accionable.
            return ToolResult(ok=False, error=f"could not read '{relativa}': it is not UTF-8 text")
        except OSError as exc:
            return _error_de_so(exc, operacion=f"could not read '{relativa}'")
        return ToolResult(ok=True, output={"path": args.get("path"), "content": content})

    def file_write(self, args: dict[str, object]) -> ToolResult:
        """Escribe (o reemplaza) un fichero del workspace: TODO o NADA.

        **El defecto que cierra, medido el 2026-08-31.** ``Path.write_text`` abre
        en modo ``"w"``, que TRUNCA al abrir; el contenido nuevo se escribe
        después. Con un fallo a media escritura —ENOSPC es el caso típico— el
        fichero quedaba con un prefijo del contenido nuevo y la tool devolvía
        ``ok=False``::

            "CONTENIDO ORIGINAL QUE IMPORTA"  ->  "NUEV"   con ok=False

        Es el mismo patrón que cerró :meth:`_ejecutar_movimiento`: destruir y
        luego decir que no se hizo nada. El agente lee «no ha pasado nada», no
        vuelve con cuidado, y el workspace queda PEOR que antes de la llamada —
        con la agravante de que aquí lo destruido es código fuente, que nadie
        echa en falta hasta que no compila.

        **El arreglo es el mismo de su hermana, y el canónico para esto**: no se
        toca el fichero real hasta tener el contenido nuevo ENTERO en disco. Se
        escribe a un hermano transitorio (:func:`_nombre_libre`) y se cambia el
        nombre con ``os.replace``, que dentro del mismo sistema de ficheros es
        atómico: quien lea el fichero ve el contenido viejo o el nuevo, nunca la
        mitad. Si falla cualquiera de los dos pasos, el transitorio se descarta y
        el fichero anterior sigue exactamente como estaba.

        **Lo que hay que conservar y no se conserva solo.** ``os.replace``
        estrena inodo, así que el fichero resultante NO hereda los permisos del
        anterior como sí hacía ``write_text``. Sin copiarlos, reescribir el
        ``spark`` de CodeIgniter o un ``.sh`` de despliegue lo deja sin el bit de
        ejecución: un defecto NUEVO introducido por el arreglo, que es la peor
        clase porque no se ve hasta que algo no arranca. Se copian antes de
        reemplazar.

        Y el caso normal no cambia: crear un fichero nuevo sigue creando de paso
        los directorios intermedios que falten, porque ``mkdir`` está bloqueado
        por el allowlist de comandos y sin eso el agente no tendría forma de
        crear un fichero en un directorio nuevo.
        """
        resolved = self._mutable_path(args.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        content = args.get("content", "")
        if not isinstance(content, str):
            return ToolResult(ok=False, error="'content' must be a string")
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._escribir_atomico(resolved, content)
        except OSError as exc:
            relativa = _relativa(resolved, Path(self.root).resolve())
            return _error_de_so(exc, operacion=f"could not write '{relativa}'")
        return ToolResult(ok=True, output={"path": args.get("path"), "bytes_written": len(content)})

    def _escribir_atomico(self, destino: Path, contenido: str) -> None:
        """Deja `contenido` en `destino` sin que exista un instante a medias.

        Aparte de :meth:`file_write` porque esto es la mecánica y allí está la
        política. Si algo falla, el transitorio se descarta y la excepción sube
        tal cual: el ``except OSError`` de quien llama la cuenta en el
        vocabulario del módulo (:func:`_error_de_so`), y el fichero anterior no
        se ha tocado.
        """
        transitorio = _nombre_libre(destino)
        try:
            transitorio.write_text(contenido, encoding="utf-8")
            if destino.is_file():
                # `os.replace` estrena inodo: sin heredar el modo, reescribir un
                # ejecutable lo deja sin el bit de ejecución. Ver `file_write`.
                os.chmod(transitorio, stat.S_IMODE(destino.stat().st_mode))
            os.replace(transitorio, destino)
        except OSError:
            self._descartar(transitorio)
            raise

    def _motivo_versionado(self, ruta: Path, root: Path) -> tuple[str, str] | None:
        """``(ruta, entrada versionada)`` si retirar ``ruta`` entera destruiría
        trabajo versionado, o ``None``.

        Una sola lectura de ``AGENT_TRACKED_PATHS`` para :meth:`_delete_tree` y
        para el destino con ``overwrite`` de :meth:`file_move`: una guarda
        duplicada envejece a media velocidad, y la mitad sin corregir pasa en
        vacío y no lo dice.

        Sólo DIRECTORIOS: de un fichero suelto ya se ocupan `write_file` /
        `delete_file`, a los que nadie considera demolición. Y a cualquier
        profundidad, en dos formas: el directorio ESTÁ en la lista, o CONTIENE
        uno que está (un temporal al que se movió `app/Config`, por ejemplo:
        borrarlo entero es el mismo destrozo en dos pasos).
        """
        if not ruta.is_dir():
            return None
        relativa = _relativa(ruta, root)
        if relativa in self._rastreadas:
            return relativa, relativa
        prefijo = relativa + "/"
        contenidas = sorted(r for r in self._rastreadas if r.startswith(prefijo))
        if contenidas:
            return relativa, contenidas[0]
        return None

    def _arbol_versionado_de_primer_nivel(self, ruta: Path, root: Path) -> str | None:
        """El árbol versionado de PRIMER NIVEL que ``ruta`` ES, o ``None``.

        Es la regla del ORIGEN de :meth:`file_move` (ADR 0164): sacar de su sitio
        un árbol de primer nivel es la forma de vaciar la raíz para un
        andamiador, y no tiene lectura de refactor. Un directorio versionado más
        profundo sí se puede mover —renombrar `app/Config` es trabajo normal—,
        y la protección lo acompaña (:meth:`_trasladar_proteccion`).
        """
        if not ruta.is_dir():
            return None
        partes = ruta.relative_to(root).parts
        if len(partes) == 1 and partes[0] in self._rastreadas:
            return partes[0]
        return None

    def _trasladar_proteccion(self, origen: Path, destino: Path, root: Path) -> None:
        """La protección sigue al contenido: lo versionado bajo `origen` pasa a
        estar bajo `destino`.

        Sin esto, «mover a un temporal y borrar el temporal» sería el rodeo a la
        guarda de :meth:`_delete_tree`, y una guarda con un rodeo al lado enseña
        el rodeo.
        """
        de = _relativa(origen, root)
        a = _relativa(destino, root)
        movidas = {r for r in self._rastreadas if r == de or r.startswith(de + "/")}
        for ruta in movidas:
            self._rastreadas.discard(ruta)
            self._rastreadas.add(a + ruta[len(de) :])

    def _delete_tree(self, resolved: Path, *, raw: object, recursive: bool) -> ToolResult:
        """La rama de DIRECTORIO de :meth:`file_delete`, aparte por legibilidad.

        Separada no por longitud sino porque son dos operaciones distintas con
        guardas distintas: borrar un fichero no puede llevarse nada por delante,
        y borrar un árbol sí.

        **El ORDEN, cerrado el 2026-08-31 con el patrón de su hermana
        :meth:`_ejecutar_movimiento`.** ``shutil.rmtree`` va DESENLAZANDO
        entradas y aborta en la primera que no puede; lo ya desenlazado no
        vuelve. La versión anterior capturaba la excepción y respondía
        ``could not delete 'vendor' [EACCES]``, así que el agente leía «no ha
        pasado nada» con media carpeta ya perdida. Es el mismo «destruir y luego
        fallar» —y en la tool que estrenó ``recursive`` el día del incidente que
        motivó todo esto—.

        Medido en Linux con la imagen real del worker y uid no root
        (``docker run --rm --user 1000:1000 agentic-platform/workers:ci``): un
        ``vendor/`` con un subdirectorio sin permiso de escritura perdió **6 de
        8 entradas** antes del ``PermissionError``. Y el camino es esperable en
        producción, no teórico: ``stack_exec`` (ADR 0093) corre el toolchain en
        OTRO contenedor, que puede dejar el árbol con dueño o permisos que este
        runtime no puede desenlazar — un ``composer install`` seguido de un
        ``delete_file vendor --recursive`` es exactamente esa secuencia.

        Así que el árbol no se destruye en su sitio: se APARTA (:func:`_apartar`,
        un renombrado) y se destruye después. Renombrar pide permiso sobre el
        directorio PADRE y no sobre el contenido, luego aparta árboles que
        ``rmtree`` no consigue desmontar; y cuando ni eso se puede, no se ha
        destruido NADA y el ``ok=False`` dice por fin la verdad.

        **Qué se responde si el descarte posterior falla, que es la decisión
        fina: ``ok=True``.** El borrado LÓGICO ya ocurrió —el árbol no está donde
        el agente pidió que no estuviera—, y responder ``ok=False`` sobre algo
        que sí pasó es este mismo defecto con el signo cambiado: el agente
        reintentaría y se encontraría un ``not found`` que no sabe interpretar.
        Es además la misma respuesta que da :meth:`_descartar` en ``move_file``
        ante el caso simétrico, y tiene que ser la misma o la familia ``file``
        contestaría dos cosas distintas al mismo suceso. Lo que queda entonces es
        un hermano oculto con el prefijo de :data:`_PREFIJO_TRANSITORIO`, donde
        está escrito su precio: sin la línea de ignore que allí se pide, el
        ``git add -A`` del cierre de tarea se lo lleva al commit.

        **La guarda del árbol versionado (2026-08-31).** Medido en vivo el mismo
        día que se estrenó ``recursive``, proyecto «Hello World CI4 v3» del
        tenant mediapro::

            delete_file {"path": "app", "recursive": true}   ->  ok, entries=85

        Esas 85 entradas eran el deliverable YA COMMITEADO de la tarea anterior
        (commit db27e13 de la rama del plan). Nada lo frenó ni lo señaló. El
        agente lo hizo por la misma presión que en su día se llevó el ``.git``:
        ``composer create-project .`` fallaba con «directorio no vacío» y quiso
        vaciarlo.

        La guarda que había protege sólo la RAÍZ, y ``app/`` no es la raíz. El
        discriminante correcto no es la profundidad sino el ESTADO EN GIT:

        * un árbol NO versionado (``vendor/``, ``node_modules/``, ``build/``) es
          un artefacto reconstruible, y borrarlo es el caso legítimo para el que
          se añadió la bandera;
        * un árbol VERSIONADO es trabajo aceptado de alguien, y borrarlo entero
          destruye el deliverable.

        El runtime no consulta git —no tiene el binario ni la rama a mano—, así
        que el worker le pasa el dato ya resuelto en ``AGENT_TRACKED_PATHS``.

        **A cualquier profundidad (auditoría 2026-09-01).** La primera versión
        cubría sólo el primer nivel, y con eso el destrozo se reconstruía con una
        llamada por subdirectorio (``app/Config``, ``app/Controllers``…). El
        worker publica ahora TODOS los directorios versionados, y se rechaza
        borrar cualquiera de ellos y cualquier directorio que CONTENGA uno (el
        temporal al que se movió `app/Config`). Lo que no está en la lista no
        está versionado —lo creó este run—, y ahí sigue viviendo el caso
        legítimo: retirar entero un módulo mal andamiado.

        **Dónde ACABA esta protección: en la familia de tools ``file``.** Vive en
        :class:`WorkspaceFiles`, así que cubre ``file_delete`` y sus hermanas —
        el camino por el que el agente toca el worktree a través del registry.
        **NO cubre ``stack_exec`` ni ``shell_exec``** (ADR 0093 / 0162): esos
        comandos los filtra la allowlist del proyecto, y un proyecto que ponga
        ``rm`` en ella sigue pudiendo hacer ``rm -rf app``. La base que la
        plataforma añade a los runs con Claude SDK ya no trae ``rm`` ni ``mv``
        (auditoría 2026-09-01): la frontera es la decisión del proyecto, no un
        regalo de la plataforma.

        La frontera es real y no un descuido: para cerrarla por ese lado haría
        falta que el worker entendiera qué borra cada comando de los 14
        toolchains, que es justo la clasificación que el ADR 0093 evitó al
        delegar en una allowlist.
        """
        if not recursive:
            return ToolResult(
                ok=False,
                error=(
                    f"is a directory, not a file: {raw}. "
                    "Pass recursive=true to remove it with everything inside."
                ),
            )
        root = Path(self.root).resolve()
        if resolved == root:
            return ToolResult(
                ok=False,
                error=(
                    "refusing to empty the workspace root: that removes the whole "
                    "deliverable, not a subtree. Delete the specific paths you mean "
                    "instead."
                ),
            )

        motivo = self._motivo_versionado(resolved, root)
        if motivo is not None:
            rastreado, entrada = motivo
            que = "it is" if rastreado == entrada else f"it contains '{entrada}', which is"
            return ToolResult(
                ok=False,
                error=(
                    f"refusing to recursively delete '{rastreado}': {que} tracked in "
                    "this branch, so it holds work already committed by an earlier "
                    "task — removing it destroys that deliverable, not a rebuildable "
                    "artifact. Delete the specific files you actually want to retire "
                    "(one delete_file call each, no recursive), or overwrite them "
                    "with write_file. If a command demands an empty directory, run it "
                    "in a subdirectory and move the result in. Untracked build trees "
                    "(vendor/, node_modules/, build/) are not affected by this."
                ),
            )
        # Se cuenta ANTES de borrar: después no hay nada que contar, y el número
        # es lo que hace legible la entrada del `steps_log`.
        entradas = sum(1 for _ in resolved.rglob("*"))
        try:
            apartado = _apartar(resolved)
        except OSError as exc:
            return _error_de_so(exc, operacion=f"could not delete '{_relativa(resolved, root)}'")
        self._descartar(apartado)
        return ToolResult(ok=True, output={"path": raw, "deleted": True, "entries": entradas})

    def file_delete(self, args: dict[str, object]) -> ToolResult:
        """Remove a file — or, with ``recursive``, a directory tree.

        El caso original (R6 / ADR 0089): reconciliar el deliverable cuando un
        intento anterior dejó un fichero rancio o duplicado en el worktree, que
        persiste entre runs. Sin esta tool no había forma de limpiarlo (`rm` y
        `git rm` los gatea el allowlist del proyecto) y las implementaciones en
        competencia nunca convergían.

        **``recursive`` (2026-08-31).** Faltaba el caso del DIRECTORIO, y no es
        raro: reinstalar dependencias pide borrar ``vendor/`` o
        ``node_modules/``, y un módulo mal andamiado se retira entero. Fichero a
        fichero eso son miles de llamadas — inviable, así que el agente acababa
        intentando ``shell_exec("rm -rf ...")``, que rebota contra el allowlist.
        Medido en vivo el 2026-08-31, en el run que instaló CodeIgniter.

        Por qué aquí y no abriendo ``rm`` en el allowlist, que era la otra
        salida:

        * ``shell_exec`` es la puerta equivocada del ADR 0162 — comparte lista
          con ``stack_exec``, así que un ``rm`` autorizado ahí confunde sobre
          qué corre dónde;
        * ``rm -rf ./*`` es ilimitado por naturaleza. Esto mantiene la jaula de
          ruta y sigue rechazando ``.git``;
        * queda AUDITADO: el ``steps_log`` guarda qué ruta se borró y cuántas
          entradas se llevó. Un ``rm`` por shell sólo dice que hubo un ``rm``;
        * el runtime ya lo gatea como ``code_changes``, así que la política de
          aprobación humana del proyecto se aplica sola.

        Y `recursive`, como el `overwrite` de `move_file`, tiene que ser un
        booleano DE VERDAD: `bool("false")` es `True`, así que la bandera que
        debía pedirse a propósito se activaba con la cadena que decía lo
        contrario. La coerción compartida está en :func:`_bandera`.

        Lo que sigue sin poder hacerse, a propósito: **vaciar la raíz del
        workspace**, y —desde el mismo día, porque la bandera se llevó por
        delante un deliverable en su primer run— **borrar entero un árbol
        VERSIONADO**, a cualquier profundidad y aunque se haya movido a un
        temporal. Ambas son operaciones cuyo resultado no es «un árbol menos»
        sino «el trabajo de otra tarea»; el detalle de la segunda está en
        :meth:`_delete_tree`. Para andamiar sobre un directorio limpio está el
        ADR 0163, que quita de en medio lo único que estorbaba.

        Ambos límites valen **sólo para la familia de tools ``file``**: por
        ``stack_exec`` (ADR 0093) el comando corre en el runtime-template con el
        worktree RW y lo único que lo filtra es la allowlist del proyecto. El
        porqué de esa frontera está en :meth:`_delete_tree`.
        """
        recursive = _bandera("recursive", args.get("recursive"))
        if isinstance(recursive, ToolResult):
            return recursive
        resolved = self._mutable_path(args.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.exists():
            return ToolResult(ok=False, error=f"not found: {args.get('path')}")

        if resolved.is_dir():
            return self._delete_tree(resolved, raw=args.get("path"), recursive=recursive)

        try:
            resolved.unlink()
        except OSError as exc:
            relativa = _relativa(resolved, Path(self.root).resolve())
            return _error_de_so(exc, operacion=f"could not delete '{relativa}'")
        return ToolResult(ok=True, output={"path": args.get("path"), "deleted": True})

    def _extremo(self, nombre: str, crudo: object) -> Path | ToolResult:
        """Resuelve UN extremo de :meth:`file_move` y etiqueta su error.

        Reutiliza :meth:`_mutable_path` —la jaula de ruta y la guarda de `.git`
        tienen que ser LAS MISMAS en origen y destino, y dos copias divergirían—
        y sólo le antepone el nombre del argumento.

        Ese prefijo no es cosmética. Con dos rutas en juego, un
        «path escapes the workspace» a secas no dice cuál de las dos arreglar, y
        un error de FORMA que el modelo no puede accionar le cuesta turnos
        enteros: en la misma ejecución del 2026-08-31, `list_files` se comió
        CATORCE contra «a non-empty 'path' is required» sin llegar a deducir qué
        le faltaba.
        """
        if not isinstance(crudo, str) or not crudo.strip():
            return ToolResult(ok=False, error=f"a non-empty '{nombre}' is required")
        resuelto = self._mutable_path(crudo)
        if isinstance(resuelto, ToolResult):
            return ToolResult(ok=False, error=f"{nombre}: {resuelto.error}")
        return resuelto

    def file_move(self, args: dict[str, object]) -> ToolResult:
        """Mueve o renombra un fichero o un árbol dentro del workspace.

        **La medición (2026-08-31), que es por qué esto existe.** Proyecto
        «Hello World CI4 v3» del tenant mediapro, modelo `gpt-oss:120b`. La tarea
        era instalar el esqueleto de CodeIgniter 4, y `composer create-project .`
        exige un directorio COMPLETAMENTE vacío. Segundo run, sobre el worktree
        que ya tenía CI4 commiteado por la tarea anterior::

             3 | composer create-project codeigniter4/framework .      -> "no vacío"
            31 | composer create-project codeigniter4/framework tmpci  -> OK
            35 | delete_file {"path":"app","recursive":true}           -> OK, 85 FICHEROS
            39 | mkdir ci4tmp                                          -> BLOQUEADO
            51 | composer create-project codeigniter4/framework .      -> sigue fallando

        El paso 31 es lo que importa: el agente llegó SOLO a la solución correcta
        —instalar en un temporal y mover el resultado— y no pudo completarla,
        porque la familia `file` era exactamente read/write/delete/list. De los
        tres pasos de su plan (instalar aparte, mover, limpiar) el único
        ejecutable era el destructivo, así que ejecutó el destructivo: esas 85
        entradas eran el deliverable ya commiteado de la tarea anterior, y
        `app/` no tenía nada de especial — era la entrada más grande de la lista.

        La guarda que rechaza borrar un árbol versionado (del mismo día) impide
        el destrozo pero NO desatasca. Esta tool es la otra mitad.

        **Por qué aquí y no abriendo `mv` en el allowlist de `shell_exec`**, que
        es exactamente el mismo argumento que se usó para el `delete_file`
        recursivo:

        * `shell_exec` es la puerta equivocada del ADR 0162 — comparte lista con
          `stack_exec`, así que un `mv` autorizado ahí confunde sobre qué corre
          dónde;
        * `mv` es ilimitado por naturaleza: `mv app /tmp` sale del worktree y
          `mv x .git` rompe el enlace del ADR 0163. Aquí la jaula de ruta y la
          guarda de `.git` aplican a LOS DOS extremos;
        * queda AUDITADO: el `steps_log` guarda origen, destino, cuántas entradas
          tenía el árbol y —si se sobrescribió— cuántas se destruyeron. Un `mv`
          por shell sólo deja constancia de que hubo un `mv`;
        * queda GATEADO como `code_changes` (`agent_runtime.approval`), así que
          la política de aprobación humana del proyecto se aplica sola. Un `mv`
          en el allowlist no pasa por ese gate ni bajo «Cliente Externo».

        **Sobrescribir se pide a propósito: `overwrite`.** `mv` significa dos
        cosas distintas según exista o no el destino, y esa ambigüedad es
        justamente lo que no puede tener una tool que un modelo invoca a ciegas.
        Aquí `destination` es SIEMPRE la ruta final; si ya está, la llamada se
        para y el error dice cómo pedirlo en serio. Mismo trato que `recursive`
        en `delete_file` y por el mismo motivo: la variante destructiva no se
        hereda del caso normal. Y el default tiene que ser el NO, porque el caso
        que motiva la tool —traer a su sitio lo que un scaffolder dejó en un
        temporal— mueve sobre rutas que todavía no existen.

        Que se pida a propósito exige además que la bandera sea un booleano DE
        VERDAD, y no lo era: `bool("false")` es `True`, así que el que decía «no»
        obtenía «sí» — y con él la puerta a todo lo destructivo de aquí abajo.
        La coerción vive en :func:`_bandera`, compartida con `delete_file`.

        **Qué pasa con el DESTINO versionado, que es la decisión fina.** La
        guarda de `AGENT_TRACKED_PATHS` mira los DOS extremos, no sólo el origen.
        El caso concreto es el del run medido: con `ci4tmp/app` recién generado
        por composer y `app/` con las 85 entradas commiteadas,
        ``move_file ci4tmp/app app --overwrite`` reemplazaría el deliverable por
        el esqueleto por defecto. Es el mismo destrozo que `delete_file app
        --recursive` —el que la guarda del mismo día acababa de rechazar— con
        otro nombre y sin dejar siquiera un borrado en el log. Si la guarda
        mirase sólo el origen, `overwrite` sería literalmente el rodeo a la
        guarda hermana, y una guarda con un rodeo al lado no protege: enseña el
        rodeo.

        **Y el solapamiento entre los dos extremos, que es donde la primera
        versión de esta tool se hizo daño a sí misma.** Un origen y un destino
        que se contienen no describen un movimiento, y con `overwrite` el
        resultado medido fue destruir los dos: lo cuenta
        :meth:`_rechazo_por_solape`. Que ninguna de esas formas pueda destruir
        nada no lo garantiza la guarda sino el ORDEN de
        :meth:`_ejecutar_movimiento` — la guarda enumera casos, y siempre queda
        uno sin enumerar.

        El límite son los ÁRBOLES versionados, no cualquier cosa versionada.
        Sobrescribir un FICHERO versionado ya se puede con `write_file`, que es
        la forma normal de editar código: bloquearlo aquí no protegería nada y sí
        convertiría la guarda en un estorbo, que es como el agente aprende a
        rodearla (la misma lección que `.gitignore` frente a `.git`). Y desde la
        auditoría del 2026-09-01 el árbol puede estar a cualquier profundidad:
        pisar `app/Config` con `overwrite` se rechaza igual que pisar `app/`; en
        cambio MOVER `app/Config` a otro sitio se permite —es un refactor— y la
        protección viaja con él (:meth:`_trasladar_proteccion`). Mover un árbol
        de PRIMER NIVEL sigue rechazado: ésa es la forma de vaciar la raíz.

        **Dónde ACABA esta protección.** En la familia de tools `file`, igual que
        la de `delete_file`: por `stack_exec` (ADR 0093) el comando corre en el
        runtime-template del proyecto con el worktree montado RW, y lo único que
        lo filtra es la allowlist de comandos del proyecto. Un proyecto que tenga
        `mv` (o `rm`, o `cp`) en su allowlist sigue pudiendo mover `app/` fuera
        de su sitio. La frontera es real y no un descuido — cerrarla por ese lado
        exigiría que el worker entendiera qué hace cada comando de los 14
        toolchains, que es justo la clasificación que el ADR 0093 evitó. Quien
        quiera la garantía completa la consigue NO poniendo esos comandos en la
        allowlist del proyecto.
        """
        # La bandera se valida ANTES de mirar el disco: si `overwrite="yes"`
        # sólo diera error cuando el destino existe, esta tool tendría el mismo
        # defecto que le reprocha a `mv` —significar dos cosas según lo que haya
        # en el destino—, y el argumento está mal escrito antes de eso.
        overwrite = _bandera("overwrite", args.get("overwrite"))
        if isinstance(overwrite, ToolResult):
            return overwrite
        origen = self._extremo("source", args.get("source"))
        if isinstance(origen, ToolResult):
            return origen
        destino = self._extremo("destination", args.get("destination"))
        if isinstance(destino, ToolResult):
            return destino

        root = Path(self.root).resolve()
        rechazo = self._rechazo_de_origen(origen, root, args.get("source"))
        if rechazo is None:
            rechazo = self._rechazo_de_destino(destino, origen, root, overwrite=overwrite)
        if rechazo is not None:
            return rechazo
        return self._ejecutar_movimiento(origen, destino, root)

    def _rechazo_de_origen(self, origen: Path, root: Path, raw: object) -> ToolResult | None:
        """Lo que impide que un movimiento SAQUE algo de donde tiene que estar.

        Aparte de :meth:`file_move` —y en dos mitades, origen y destino— porque
        el linter tenía razón: una sola función con siete motivos de rechazo se
        lee como una lista de casos sueltos y no como dos preguntas distintas.
        Las preguntas son «¿puede esto salir de aquí?» y «¿puede aquello ser
        tapado?», y cada una tiene su respuesta.
        """
        if origen == root:
            return ToolResult(
                ok=False,
                error=(
                    "refusing to move the workspace root: that empties the whole "
                    "deliverable, not a subtree. Move the specific paths you mean "
                    "instead."
                ),
            )
        if not origen.exists():
            return ToolResult(ok=False, error=f"not found: {raw}")

        rastreado = self._arbol_versionado_de_primer_nivel(origen, root)
        if rastreado is not None:
            return ToolResult(
                ok=False,
                error=(
                    f"refusing to move '{rastreado}' out of its place: it is tracked "
                    "in this branch, so it holds work already committed by an earlier "
                    "task — moving the whole tree away destroys that deliverable just "
                    "as deleting it would. Move the specific files you actually mean "
                    "(one move_file call each), or scaffold into a temporary directory "
                    "and move its entries in one by one. Untracked trees (vendor/, "
                    "node_modules/, a freshly generated ci4tmp/) are not affected."
                ),
            )
        return None

    def _rechazo_de_destino(
        self, destino: Path, origen: Path, root: Path, *, overwrite: bool
    ) -> ToolResult | None:
        """Lo que impide que un movimiento TAPE algo que ya estaba."""
        if destino == root:
            # El intento natural tras `create-project ci4tmp` es «vuelca esto en
            # la raíz», y eso no es UN movimiento: es fusionar dos árboles, con
            # la mitad de las colisiones apuntando al deliverable versionado. Se
            # rechaza, pero enseñando la vía que SÍ funciona — si el «no» no trae
            # salida, el modelo prueba variantes hasta agotar el presupuesto.
            return ToolResult(
                ok=False,
                error=(
                    "refusing to move onto the workspace root: merging two trees is "
                    "not a single move. Move them in one entry at a time (one "
                    "move_file call per entry; list_files on the source gives you "
                    "the list), so each collision with existing work is decided "
                    "separately."
                ),
            )
        solape = self._rechazo_por_solape(destino, origen, root)
        if solape is not None:
            return solape
        if not destino.exists():
            return None
        if not overwrite:
            return ToolResult(
                ok=False,
                error=(
                    f"destination already exists: {_relativa(destino, root)}. "
                    "Replacing it is the destructive variant of a move, so it is asked "
                    "for on purpose: pass overwrite=true, or choose a path that does "
                    "not exist yet."
                ),
            )
        motivo = self._motivo_versionado(destino, root)
        if motivo is not None:
            pisado, entrada = motivo
            que = "it is" if pisado == entrada else f"it contains '{entrada}', which is"
            return ToolResult(
                ok=False,
                error=(
                    f"refusing to overwrite '{pisado}': {que} tracked in this branch, "
                    "so it holds work already committed by an earlier task — replacing "
                    "the whole tree destroys that deliverable exactly like deleting it "
                    "would, and does not even leave a delete in the log. Move the "
                    "specific files you actually mean (one move_file call each), or "
                    "retire with delete_file the parts you really want gone, one at a "
                    "time."
                ),
            )
        return None

    def _rechazo_por_solape(self, destino: Path, origen: Path, root: Path) -> ToolResult | None:
        """Los TRES solapamientos entre origen y destino, que no son un movimiento.

        **La mitad que faltaba, medida por una verificación adversarial el
        2026-08-31 — y era peor que el incidente que este arreglo vino a
        resolver.** La guarda original miraba un solo sentido (``origen in
        destino.parents``). Con el destino como ANCESTRO del origen y
        ``overwrite``, :meth:`_ejecutar_movimiento` hacía ``rmtree(destino)``,
        que se llevaba el origen por delante —está DENTRO—, y el ``shutil.move``
        siguiente fallaba porque ya no quedaba nada que mover::

            move_file ci4tmp/app -> ci4tmp        (overwrite)  ->  ok=False, 0 ficheros
            move_file app/Config/Boot -> app/Config (overwrite) -> ok=False, app/ VACÍA

        El segundo destruyó 41 ficheros commiteados. Y la tool devolvía
        ``ok=False``, así que el agente leía «no ha pasado nada»: peor que el
        borrado original, que al menos constaba en el ``steps_log`` como un
        borrado con su recuento.

        La guarda de árbol versionado no basta como red: cubre ``app/Config``
        sólo si está versionado, y un temporal recién creado en este run no lo
        está. Para lo no versionado, aquí no hay red debajo.

        Los tres casos, con su salida, porque un «no» sin salida hace que el
        modelo pruebe variantes hasta agotar el presupuesto:

        * **la misma ruta** — con ``overwrite`` sería un ``rmtree`` del propio
          origen y ningún movimiento; no hay nada que pedir aquí;
        * **el destino cuelga del origen** — mover un árbol dentro de sí mismo;
        * **el origen cuelga del destino** — «aplanar» un temporal sobre su
          padre. Es el caso medido, y la salida es mover a una ruta FUERA del
          destino y retirar después lo que sobre.
        """
        if origen == destino:
            return ToolResult(
                ok=False,
                error=(
                    f"source and destination are the same path ('{_relativa(origen, root)}'): "
                    "there is nothing to move. If you meant to replace what is inside it, "
                    "move in the entries you actually want (one move_file call each)."
                ),
            )
        if origen in destino.parents:
            return ToolResult(
                ok=False,
                error=(
                    f"cannot move '{_relativa(origen, root)}' into itself: the destination "
                    f"'{_relativa(destino, root)}' is inside it. Pick a destination outside "
                    "the tree you are moving."
                ),
            )
        if destino in origen.parents:
            afuera = _relativa(destino.parent / origen.name, root)
            return ToolResult(
                ok=False,
                error=(
                    f"cannot move '{_relativa(origen, root)}' onto '{_relativa(destino, root)}', "
                    "which contains it: replacing the destination would destroy the source "
                    f"along with it. Move it outside first (for example to '{afuera}'), then "
                    "delete with delete_file what is left over."
                ),
            )
        return None

    def _ejecutar_movimiento(self, origen: Path, destino: Path, root: Path) -> ToolResult:
        """El movimiento en sí, ya validado, y el registro de lo que hizo.

        **El orden es la garantía, no la guarda.** La versión anterior destruía
        el destino (``rmtree`` / ``unlink``) y DESPUÉS movía. Cuando el
        movimiento fallaba —cualquier motivo: el solape que se coló por la mitad
        que faltaba de :meth:`_rechazo_por_solape`, ENOSPC, EACCES, un fichero
        bloqueado por otro proceso en Windows— la tool devolvía ``ok=False`` con
        el destino ya destruido. Que una operación pueda destruir y luego decir
        que no hizo nada es un defecto de FORMA: ninguna guarda lo arregla,
        porque siempre queda otro motivo de fallo que la guarda no enumera.

        Así que el destino no se destruye: se APARTA (:func:`_apartar`, un
        renombrado a su lado) y sólo se descarta cuando el movimiento YA ha ido
        bien. Si falla, se pone de vuelta y se devuelve el error con el destino
        intacto — y el origen también, porque ``shutil.move`` no lo toca hasta
        que puede completar.

        **Dónde NO se puede garantizar, dicho exactamente.** Quedan dos huecos, y
        los dos dejan los datos EXISTIENTES, nunca destruidos:

        1. si el propio rescate falla (el renombrado de vuelta), el árbol
           original sigue entero bajo el nombre apartado, y el error lo dice con
           su ruta para que se pueda recuperar a mano;
        2. si el descarte final del destino apartado falla (en Windows, ficheros
           de sólo lectura o abiertos por otro proceso), el movimiento YA
           ocurrió: se devuelve ``ok=True`` —decir que falló sería el defecto
           inverso, y el mismo— y lo apartado se queda como hermano oculto con el
           prefijo de :data:`_PREFIJO_TRANSITORIO`, donde está escrito el precio
           y qué hay que tocar para no pagarlo (un ``git add -A`` se lo llevaría
           al commit). No se anuncia en la salida porque el catálogo
           declara la de ``move_file`` con ``additionalProperties: false`` y
           ``tests/unit/test_move_file_catalogo_y_reparto.py`` comprueba que el
           ejecutor no devuelve ninguna clave que el catálogo no declare: el día
           que se quiera contar, primero se añade el campo a esa fila.

        Fuera de eso, ``shutil.move`` dentro del workspace es un ``rename`` en el
        mismo sistema de ficheros; su respaldo de copiar-y-borrar (que sí podría
        dejar un destino a medias) exigiría un punto de montaje distinto DENTRO
        del worktree, que este runtime no monta.
        """
        reemplazaba = destino.exists()
        # Se cuenta ANTES de tocar nada: después no hay nada que contar, y esos
        # números son lo único que distingue «pisó un directorio vacío» de
        # «evaporó 85 ficheros» cuando alguien lea el `steps_log` a posteriori.
        reemplazadas = (
            sum(1 for _ in destino.rglob("*")) if reemplazaba and destino.is_dir() else None
        )
        entradas = sum(1 for _ in origen.rglob("*")) if origen.is_dir() else None

        apartado: Path | None = None
        try:
            # `mkdir` está BLOQUEADO por el allowlist de comandos (paso 39 del run
            # medido): si esta tool exigiera que el padre existiera, el agente se
            # quedaría con el plan correcto y sin forma de ejecutarlo.
            destino.parent.mkdir(parents=True, exist_ok=True)
            if reemplazaba:
                apartado = _apartar(destino)
            shutil.move(str(origen), str(destino))
        except OSError as exc:
            fallo = _error_de_so(
                exc,
                operacion=(
                    f"could not move '{_relativa(origen, root)}' onto '{_relativa(destino, root)}'"
                ),
            )
            if apartado is not None:
                return self._restaurar(apartado, destino, root, fallo)
            return fallo

        if apartado is not None:
            self._descartar(apartado)
        # Lo versionado que viajaba dentro de `origen` sigue versionado en su
        # nueva ruta, y la guarda tiene que seguirlo. Ver `_trasladar_proteccion`.
        self._trasladar_proteccion(origen, destino, root)

        # Se registran las rutas EFECTIVAS, no la grafía que mandó el modelo:
        # `./ci4tmp/` y `ci4tmp` son la misma carpeta, y el log tiene que poder
        # leerse sin resolverlo a mano.
        salida: dict[str, object] = {
            "source": _relativa(origen, root),
            "destination": _relativa(destino, root),
            "moved": True,
        }
        if entradas is not None:
            salida["entries"] = entradas
        if reemplazaba:
            salida["replaced"] = True
            if reemplazadas is not None:
                salida["replaced_entries"] = reemplazadas
        return ToolResult(ok=True, output=salida)

    def _restaurar(
        self, apartado: Path, destino: Path, root: Path, fallo: ToolResult
    ) -> ToolResult:
        """Devuelve a su sitio el destino apartado tras un movimiento fallido.

        Si ni eso se puede, el árbol SIGUE EXISTIENDO bajo el nombre apartado y
        el error lo dice con su ruta: un dato recuperable a mano es otra cosa
        que un dato perdido, y callarlo sería dejar al agente creyendo que su
        destino desapareció.
        """
        try:
            apartado.rename(destino)
        except OSError as exc:
            return ToolResult(
                ok=False,
                error=(
                    f"{fallo.error}; the destination could not be put back either, "
                    f"it is still there under '{_relativa(apartado, root)}' "
                    f"[{_codigo_estable(exc)}]"
                ),
            )
        return fallo

    def _descartar(self, apartado: Path) -> None:
        """Retira un hermano transitorio UNA VEZ la operación ha ido bien.

        Compartida por las tres tools que mutan el workspace, y no copiada en
        cada una: el destino apartado de :meth:`_ejecutar_movimiento`, el árbol
        apartado de :meth:`_delete_tree` y el temporal de
        :meth:`_escribir_atomico` son el mismo objeto —algo que sólo existe
        mientras la operación real no está confirmada— y merecen exactamente el
        mismo trato. Tres copias de esta decisión envejecerían a distinta
        velocidad.

        Que no se pueda retirar NO convierte la llamada en un fallo: la
        operación ya ocurrió, y devolver ``ok=False`` sobre algo que sí pasó es
        exactamente el defecto que este orden viene a cerrar, con el signo
        cambiado. Lo que queda entonces —y su precio— está en el punto 2 de
        :meth:`_ejecutar_movimiento` y en :data:`_PREFIJO_TRANSITORIO`.

        En el camino del temporal de escritura hay además un caso que no es un
        resto sino una ausencia: si ``write_text`` falló al ABRIR, el temporal no
        llegó a existir. El ``unlink`` levanta ``FileNotFoundError``, que es un
        ``OSError``, y se traga por el mismo sitio.

        **Antes de rendirse, da permiso y reintenta** (auditoría 2026-09-01). Los
        dos motivos por los que un ``rmtree`` a secas no puede —un fichero de
        sólo lectura, un directorio sin permiso de escritura dejado por otro
        contenedor— se resuelven con un ``chmod``. Y el residuo que se dejaba en
        su lugar tenía un precio que nadie había medido: el ``git clean`` de la
        provisión siguiente intentaba borrarlo, no podía, y la tarea quedaba
        `workspace_unavailable` en cada reintento.
        """
        try:
            if apartado.is_dir() and not apartado.is_symlink():
                _rmtree_forzado(apartado)
            else:
                _desenlazar_forzado(apartado)
        except OSError:
            return

    def file_list(self, args: dict[str, object]) -> ToolResult:
        """Lista el workspace filtrando por un glob; sin nada, el directorio pedido.

        **El defecto que cierra (medido el 2026-09-01).** El esquema de la tool
        —lo único que el modelo ve— anunciaba «List files matching a glob pattern
        under a path» con ``pattern`` entre sus propiedades, y este método NUNCA
        leía ``pattern``: hacía ``resolved.iterdir()``, un listado plano de un
        nivel, sin filtrar. En un run real del proyecto `Hello World CI4 v3`
        (tenant mediapro) hubo 15 llamadas con patrón no trivial y las 15
        devolvieron el mismo listado plano sin avisar de nada:

            list_files {"path":".", "pattern":"tests/**/*.php"}     -> [{"name":"docs"}]
            list_files {"path":".", "pattern":"*phpunit*"}          -> [{"name":"docs"}]
            list_files {"path":".", "pattern":"vendor/bin/phpunit"} -> [{"name":"docs"}]

        El agente probó ocho patrones distintos buscando los tests, recibió la
        misma respuesta ocho veces y no pudo concluir nada — por eso repetía. Es
        la misma familia que el ``path`` vacío que se cerró el 2026-08-31, pero
        PEOR: aquél devolvía un error, y éste devolvía un resultado plausible.

        Las cuatro decisiones del contrato, y por qué son ésas. Todas salen de
        mirar los patrones que el modelo manda DE VERDAD — las 965 llamadas a
        ``list_files`` del ``steps_log``, contadas el 2026-09-01:

            **/*  316 | *  279 | **/*.php  72 | **/*.md  69 | *.php  32 | **  8
            589 llevan '/'    | 578 llevan '**' | 39 llevan llaves | 33 sin comodín

        **1. Qué significa `pattern`.** Un glob RELATIVO a ``path`` que casa
        contra la RUTA relativa de cada entrada, no contra su nombre: 589 de 965
        patrones reales llevan ``/`` (``tests/**/*.php``,
        ``app/Config/Routes.php``), y casar contra el nombre los dejaría todos en
        vacío. ``*``, ``?`` y ``[...]`` NO cruzan la barra; **sólo ``**``
        desciende**, porque ``*`` a secas es el segundo patrón más usado (279
        llamadas) y ahí significa «enséñame este directorio»: hacerlo recursivo
        devolvería el árbol entero justo en el caso más frecuente. Las llaves
        ``{json,lock}`` se expanden (39 llamadas reales las usan y `pathlib` no
        las entiende). Y **distingue mayúsculas**, porque el runtime va sobre
        Linux y el repo es PSR-4: casar ``home.php`` con ``Home.php`` le daría al
        agente una ruta que después ``read_file`` no encuentra.

        **2. Cuál es el default.** ``*`` — plano —, y el esquema del catálogo
        pasa a decirlo. Anunciaba ``"**/*"``, y CUMPLIRLO habría sido peor que el
        defecto: un ``list_files`` sobre la raíz de un CodeIgniter devolvería los
        ~5.000 ficheros de ``vendor/`` en una sola llamada, y la rama del plan
        del incidente llegó a tener 10.318. Corregir el contrato mintiendo por el
        otro lado no vale, así que se corrige el esquema, no la implementación:
        el default efectivo es el que ya había.

        **3. El tope, que es la parte crítica.** :data:`_MAX_LIST_ENTRIES`
        entradas (500, justificado allí con la distribución real), y **el
        resultado dice que se truncó, cuántas había y cómo acotar**. Un truncado
        silencioso es exactamente el defecto que este método arregla con otra
        cara: el agente creería que no hay más ficheros. Por lo mismo,
        ``truncated`` viaja SIEMPRE —también en ``False``—, porque la ausencia de
        una señal es ambigua y la ambigüedad es lo que le hizo repetir.

        **4. Un patrón que no se puede cumplir se RECHAZA.** Ignorarlo es
        literalmente el defecto (no-string), y devolver ``[]`` sobre un patrón
        que no se llegó a interpretar (llaves o corchetes sin cerrar) o que
        apunta fuera del workspace (``/etc/**``, ``../``) diría «ese fichero no
        existe» en vez de «esa pregunta no se puede contestar». Los errores
        llevan la forma válida escrita. La única laxitud es la simétrica al
        arreglo del ``path``: un ``pattern`` vacío, en blanco o ``null`` es el
        default, porque el modelo manda las claves VACÍAS en vez de omitirlas
        —medido: ``{"path": "", "pattern": "*"}`` doce veces— y «filtra por lo
        que sea» sí tiene una interpretación obvia.

        **Por qué el default de `path` vive aquí y no en `_safe_path`
        (2026-08-31).** Medido en la ejecución
        ``01a05881-89d7-79fa-be72-bd0e7c1a9fbb``: de sus 22 ``list_files``,
        CATORCE fueron rechazadas con «a non-empty 'path' is required». El agente
        quería listar el workspace —la operación más obvia que existe— y se comió
        el 60% del presupuesto del run chocando contra un requisito de FORMA. Se
        arregla SÓLO aquí a propósito: ``_safe_path`` lo comparten ``file_read``
        / ``file_write`` / ``file_delete``, donde un path vacío tiene que seguir
        siendo un error — «lista lo que sea» tiene interpretación obvia, «borra lo
        que sea» no la tiene.
        """
        crudo = args.get("path")
        if crudo is None or (isinstance(crudo, str) and not crudo.strip()):
            crudo = "."
        resolved = self._safe_path(crudo)
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.is_dir():
            return ToolResult(ok=False, error=f"not a directory: {crudo}")

        patron_crudo = args.get("pattern")
        if patron_crudo is None or (isinstance(patron_crudo, str) and not patron_crudo.strip()):
            patron = _DEFAULT_LIST_PATTERN
        elif not isinstance(patron_crudo, str):
            return ToolResult(
                ok=False,
                error=(
                    "'pattern' must be a string glob relative to 'path' (e.g. "
                    f"'**/*.php'); got {type(patron_crudo).__name__}"
                ),
            )
        else:
            patron = patron_crudo.strip()
        try:
            glob = _compilar_glob(patron)
        except _PatronInvalidoError as exc:
            return ToolResult(ok=False, error=str(exc))

        coincidencias, escaneadas, ilegibles = _recorrer(resolved, glob)
        total = len(coincidencias)
        truncado = total > _MAX_LIST_ENTRIES
        entries = [_entrada(relativa, ruta) for relativa, ruta in coincidencias[:_MAX_LIST_ENTRIES]]

        notas: list[str] = []
        if truncado:
            notas.append(
                f"showing the first {_MAX_LIST_ENTRIES} of {total} matching entries, "
                f"sorted by path. There ARE more than the ones listed: narrow the "
                f"'pattern' (e.g. 'app/**/*.php' instead of '**/*') or point 'path' "
                f"at a subdirectory."
            )
        elif total == 0:
            notas.append(_nada_casa(crudo, patron, glob, escaneadas))
        if ilegibles:
            notas.append(
                f"{ilegibles} director{'y' if ilegibles == 1 else 'ies'} could not be "
                f"read and {'was' if ilegibles == 1 else 'were'} skipped, so entries "
                f"under {'it' if ilegibles == 1 else 'them'} are not listed."
            )

        # Se devuelven la ruta y el patrón EFECTIVOS, no los que mandó el modelo:
        # si pidió "" y se listó ".", el `steps_log` tiene que decir qué se listó
        # de verdad y con qué filtro.
        salida: dict[str, object] = {
            "path": crudo,
            "pattern": patron,
            "entries": entries,
            "truncated": truncado,
            "total_matches": total,
        }
        if notas:
            salida["note"] = " ".join(notas)
        return ToolResult(ok=True, output=salida)
