"""El prompt de voz del córtex fija el idioma de la RESPUESTA al de la voz elegida.

Regresión reportada en vivo: el operador ponía una voz española (``ef_dora``) y el
córtex le contestaba en inglés (gpt-oss razona en inglés y arrastraba el idioma a la
respuesta). El fix ata el idioma de salida a la voz: ``_cortex_voice_base_prompt``
recibe una ``language_instruction`` imperativa y la incrusta en el system prompt.
"""

from __future__ import annotations

import pytest
from api_server.cortex.voice_turn import _cortex_voice_base_prompt
from api_server.routers.assistant_voice import voice_language_instruction

pytestmark = pytest.mark.unit


def test_spanish_instruction_is_injected() -> None:
    es = voice_language_instruction("ef_dora")
    prompt = _cortex_voice_base_prompt(web_enabled=False, language_instruction=es)
    assert es in prompt
    assert "nunca respondas en inglés" in prompt.lower()


def test_english_instruction_is_injected() -> None:
    en = voice_language_instruction("am_michael")
    prompt = _cortex_voice_base_prompt(web_enabled=False, language_instruction=en)
    assert en in prompt


def test_without_instruction_falls_back_to_owner_language() -> None:
    """Sin voz conocida (compat): se mantiene el copy neutro de antes."""
    prompt = _cortex_voice_base_prompt(web_enabled=False)
    assert "idioma del owner" in prompt


def test_web_note_still_appended_after_language() -> None:
    """El idioma no debe pisar la nota de acceso web (ambos coexisten)."""
    es = voice_language_instruction("ef_dora")
    prompt = _cortex_voice_base_prompt(web_enabled=True, language_instruction=es)
    assert es in prompt
    assert "web_search" in prompt
