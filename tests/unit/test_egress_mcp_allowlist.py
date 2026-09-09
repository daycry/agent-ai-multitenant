"""La allowlist de hosts MCP remotos: validador y renderizador (`task_mk_02`, ADR 0165).

El ajuste guarda **hostnames en claro, nunca regexes**, y el renderizador emite la
línea del filtro de tinyproxy. Dos propiedades sostienen todo lo demás y por eso
se fijan aquí antes de que exista el código:

- **Anclado y escapado** (D1). Sin `^…$`, `mcp.atlassian.com` casaría
  `evil-mcp.atlassian.com.attacker.tld`; sin escapar el punto, casaría
  `mcpXatlassianYcom`. Y NO se usa `re.escape` a pelo: emite `\\-`, y en POSIX ERE
  una barra invertida delante de un carácter ordinario es comportamiento
  indefinido. Como el validador reduce el alfabeto a `[a-z0-9.-]`, el único
  metacarácter posible es el punto.
- **Alfabeto cerrado** (D2). Es lo que impide que un operador —o alguien que le
  dicte la entrada— meta `.*` y abra el proxy entero.

Y una corrección medida del addendum del ADR (A3): IDNA **no** rechaza un
homógrafo unicode, lo convierte en punycode válido: un `paypal.com` con dos
letras cirílicas sale `xn--ypal-43d9g.com`, que pasaría el alfabeto sin despeinarse. Así que lo
no-ASCII se rechaza pidiendo el punycode explícito, que es una decisión que el
operador puede auditar leyendo.
"""

from __future__ import annotations

import pytest
from api_server.egress.mcp_allowlist import (
    MAX_HOSTS,
    InvalidMcpHostError,
    normalise_host,
    render_filter_line,
    render_generated_block,
)

#: `paypal.com` con la `p` y la `a` CIRÍLICAS. Se compone con `chr()` a propósito:
#: un literal homógrafo en el código fuente es exactamente el problema que este
#: caso prueba, y además ruff lo rechaza (RUF001) — con razón.
_HOMOGRAFO = chr(0x0440) + chr(0x0430) + "ypal.com"

pytestmark = pytest.mark.unit


# --------------------------------------------------------------- normalización


@pytest.mark.parametrize(
    ("entrada", "canonico"),
    [
        ("mcp.atlassian.com", "mcp.atlassian.com"),
        ("  mcp.atlassian.com  ", "mcp.atlassian.com"),
        ("MCP.Atlassian.COM", "mcp.atlassian.com"),
        ("api.githubcopilot.com", "api.githubcopilot.com"),
        ("xn--ypal-43d9g.com", "xn--ypal-43d9g.com"),
        ("a-b.example.com", "a-b.example.com"),
    ],
)
def test_un_host_legitimo_se_normaliza_a_minusculas(entrada: str, canonico: str) -> None:
    assert normalise_host(entrada) == canonico


@pytest.mark.parametrize(
    ("entrada", "motivo"),
    [
        ("", "vací"),
        ("   ", "vací"),
        ("vault", "punto"),
        ("localhost", "punto"),
        ("10.0.0.5", "IP"),
        ("169.254.169.254", "IP"),
        ("[::1]", "IP"),  # lo caza la regla de IP literal, que es más precisa
        ("mcp.atlassian.com:8443", "alfabeto"),
        ("https://mcp.atlassian.com", "alfabeto"),
        (".*", "alfabeto"),
        ("mcp.atlassian.com/path", "alfabeto"),
        ("-mcp.atlassian.com", "etiqueta"),
        ("mcp-.atlassian.com", "etiqueta"),
        ("metadata.google.internal", "bloqueado"),
        ("cosa.internal", "bloqueado"),
        ("cosa.local", "bloqueado"),
        # `paypal.com` con la `p` y la `a` cirílicas, escrito con escapes a propósito:
        # el literal dispara RUF001 y, sobre todo, así se ve que son otras letras.
        (_HOMOGRAFO, "ASCII"),
    ],
)
def test_lo_que_no_puede_entrar_no_entra(entrada: str, motivo: str) -> None:
    with pytest.raises(InvalidMcpHostError) as exc:
        normalise_host(entrada)
    assert exc.value.reason, "el rechazo tiene que decir POR QUÉ, no ser un booleano"
    assert motivo.lower() in exc.value.reason.lower(), (
        f"el motivo de {entrada!r} no menciona «{motivo}»: {exc.value.reason}"
    )


def test_una_etiqueta_de_mas_de_63_caracteres_se_rechaza_sin_reventar() -> None:
    """`str.encode('idna')` levanta `UnicodeError`, no `ValueError`: sin capturarlo
    el PUT del ajuste devolvería 500 en vez del 422 con su motivo."""
    largo = "a" * 64 + ".example.com"
    with pytest.raises(InvalidMcpHostError):
        normalise_host(largo)


def test_un_host_de_mas_de_253_caracteres_se_rechaza() -> None:
    largo = ".".join(["a" * 60] * 5) + ".com"
    with pytest.raises(InvalidMcpHostError):
        normalise_host(largo)


# --------------------------------------------------------------- renderizado


def test_la_linea_va_anclada_y_con_el_punto_escapado() -> None:
    assert render_filter_line("mcp.atlassian.com") == r"^mcp\.atlassian\.com$"


def test_el_guion_no_se_escapa_porque_en_ere_eso_es_indefinido() -> None:
    """`re.escape('a-b.example.com')` emite `a\\-b\\.example\\.com`; glibc y musl lo
    toleran hoy, el contrato POSIX no lo garantiza."""
    linea = render_filter_line("a-b.example.com")
    assert linea == r"^a-b\.example\.com$"
    assert "\\-" not in linea


def test_el_anclado_impide_el_sufijo_atacante() -> None:
    import re

    patron = re.compile(render_filter_line("mcp.atlassian.com"))
    assert patron.fullmatch("mcp.atlassian.com")
    assert not patron.match("evil-mcp.atlassian.com.attacker.tld")
    assert not patron.match("mcpXatlassianYcom")


def test_el_bloque_generado_lleva_centinelas_y_una_linea_por_host() -> None:
    bloque = render_generated_block(["mcp.atlassian.com", "api.github.com"])
    lineas = bloque.splitlines()

    assert lineas[0].startswith("# >>> BEGIN generated"), lineas[0]
    assert lineas[-1].startswith("# <<< END generated"), lineas[-1]
    assert r"^mcp\.atlassian\.com$" in lineas
    assert r"^api\.github\.com$" in lineas
    assert "NO EDITAR" in bloque, "el bloque tiene que decir que lo reescribe un script"


def test_el_bloque_vacio_sigue_teniendo_sus_centinelas() -> None:
    """Si el bloque desapareciera al quedarse sin hosts, el renderizador no
    sabría dónde volver a escribir y acabaría añadiendo un segundo bloque."""
    bloque = render_generated_block([])

    assert bloque.splitlines()[0].startswith("# >>> BEGIN generated")
    assert bloque.splitlines()[-1].startswith("# <<< END generated")


def test_los_hosts_salen_ordenados_y_sin_duplicados() -> None:
    bloque = render_generated_block(["b.example.com", "a.example.com", "B.example.com"])
    patrones = [linea for linea in bloque.splitlines() if linea.startswith("^")]

    assert patrones == [r"^a\.example\.com$", r"^b\.example\.com$"]


def test_hay_un_tope_y_se_dice_cual() -> None:
    """No por rendimiento —tinyproxy compila las regex una vez— sino porque una
    allowlist que nadie puede leer de un vistazo ha dejado de ser una allowlist."""
    assert MAX_HOSTS == 100
    with pytest.raises(InvalidMcpHostError) as exc:
        render_generated_block([f"h{i}.example.com" for i in range(MAX_HOSTS + 1)])
    assert str(MAX_HOSTS) in exc.value.reason
