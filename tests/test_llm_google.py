from __future__ import annotations

from agent_economy.llm_google import _text_config_for_model


def test_text_config_disables_automatic_function_calling() -> None:
    cfg = _text_config_for_model(
        model="models/gemini-3-pro-preview",
        system="sys",
        temperature=0.0,
        max_output_tokens=2000,
    )
    assert cfg.response_mime_type == "text/plain"
    assert cfg.automatic_function_calling is not None
    assert cfg.automatic_function_calling.disable is True
