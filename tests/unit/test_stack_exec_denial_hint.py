"""El mensaje de denegación tiene que ser ACCIONABLE (G6b, `guardas-research`).

El fallo medido: un agente que quería mirar 50 líneas de un fichero recibía
«command not allowed: sed» y caía en releer el fichero ENTERO una y otra vez —
la read-churn que disparaba las guardas de esterilidad y bloqueó la tarea
«Auditar dependencias». Un «no» sin alternativa empuja al agente al peor camino.

El detalle que el propio plan tenía mal: la alternativa "obvia" que proponía era
`head -n N | tail`, y eso lleva TUBERÍA — que `stack_exec` tampoco admite. Se
ofrece `read_file` con offset/limit, que no pasa por la allowlist y siempre
funciona.
"""

from __future__ import annotations

from workers.tasks.stack_exec_task import _stack_command_allowed

_ALLOWED = ["php", "composer"]


def test_an_allowed_command_passes() -> None:
    assert _stack_command_allowed("php -v", _ALLOWED) is None


def test_a_denied_read_utility_points_at_read_file() -> None:
    msg = _stack_command_allowed("sed -n '1,50p' src/Routes.php", _ALLOWED)
    assert msg is not None
    assert msg.startswith("command not allowed: sed")
    assert "read_file" in msg
    assert "offset" in msg


def test_the_hint_does_not_suggest_a_pipe() -> None:
    # `stack_exec` corre UN programa por llamada. Sugerir `head -n N | tail`
    # mandaría al agente directo a un segundo fallo.
    msg = _stack_command_allowed("head -n 50 f.php", _ALLOWED)
    assert msg is not None
    assert "|" not in msg.split("Allowed:")[0]


def test_shell_chaining_still_gets_its_own_explanation() -> None:
    # Ojo con el caso de prueba: la guarda mira el PROGRAMA (argv[0]). Con
    # `php -v && ...` el programa es `php`, que está permitido, así que ni
    # siquiera se llega a denegar. El aviso de encadenado es para cuando el
    # programa en sí no pasa — típicamente un `bash -c`.
    msg = _stack_command_allowed("bash -c 'php -v && composer install'", _ALLOWED)
    assert msg is not None
    assert "shell chaining" in msg


def test_a_non_read_command_gets_no_read_file_hint() -> None:
    # No vacuo: si el consejo saliera para todo, dejaría de ser un consejo.
    msg = _stack_command_allowed("rm -rf /", _ALLOWED)
    assert msg is not None
    assert "read_file" not in msg


def test_the_denial_always_lists_what_IS_allowed() -> None:
    msg = _stack_command_allowed("sed -n 1p f", _ALLOWED)
    assert msg is not None
    assert "php" in msg and "composer" in msg


def test_an_empty_allowlist_says_so_instead_of_showing_nothing() -> None:
    msg = _stack_command_allowed("php -v", [])
    assert msg is not None
    assert "none configured" in msg
