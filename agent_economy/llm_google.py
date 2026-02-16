from __future__ import annotations

import json
import os
import random
import time
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel

from agent_economy.json_extract import extract_json_object
from agent_economy.llm_openai import Usage, _resolve_max_retries

_TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_TRANSIENT_CODE_NAMES = {
    "deadline_exceeded",
    "resource_exhausted",
    "service_unavailable",
    "unavailable",
    "internal",
}


def _status_code_from_error(err: Exception) -> int | None:
    raw = getattr(err, "status_code", None)
    if raw is None:
        response = getattr(err, "response", None)
        raw = getattr(response, "status_code", None)
    try:
        return int(raw) if raw is not None else None
    except Exception:
        return None


def _code_name_from_error(err: Exception) -> str | None:
    code = getattr(err, "code", None)
    if code is None:
        return None
    name = getattr(code, "name", None)
    if name:
        return str(name).strip().lower()
    try:
        return str(code).strip().lower()
    except Exception:
        return None


def _is_transient_google_error(err: Exception) -> bool:
    if isinstance(err, TimeoutError):
        return True
    status = _status_code_from_error(err)
    if status is not None:
        return status in _TRANSIENT_STATUS_CODES
    code_name = _code_name_from_error(err)
    if code_name in _TRANSIENT_CODE_NAMES:
        return True
    name = type(err).__name__.lower()
    if (
        "ratelimit" in name
        or "resourceexhausted" in name
        or "timeout" in name
        or "connection" in name
    ):
        return True
    return False


def _usage_from_response(resp: Any) -> Usage:
    usage = getattr(resp, "usage_metadata", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage_metadata")
    if usage is None:
        return Usage(calls=1, input_tokens=0, output_tokens=0)

    input_tokens = getattr(usage, "prompt_token_count", None)
    if input_tokens is None and isinstance(usage, dict):
        input_tokens = usage.get("prompt_token_count", 0)

    output_tokens = getattr(usage, "candidates_token_count", None)
    if output_tokens is None and isinstance(usage, dict):
        output_tokens = usage.get("candidates_token_count", 0)

    thoughts_tokens = getattr(usage, "thoughts_token_count", None)
    if thoughts_tokens is None and isinstance(usage, dict):
        thoughts_tokens = usage.get("thoughts_token_count", 0)

    return Usage(
        calls=1,
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0) + int(thoughts_tokens or 0),
    )


def _thinking_config_for_model(model: str) -> types.ThinkingConfig | None:
    m = str(model).lower()
    if "gemini-3" in m:
        return types.ThinkingConfig(thinking_level="low", include_thoughts=False)
    return None


class GoogleJSONClient:
    def __init__(self, *, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    def call_text(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_output_tokens: int = 3000,
        reasoning_effort: str | None = None,
        text_verbosity: str | None = None,
        max_retries: int = 3,
    ) -> tuple[str, Usage]:
        _ = reasoning_effort, text_verbosity
        max_retries = _resolve_max_retries(
            requested=max_retries,
            env_retries_raw=(
                os.getenv("AE_GOOGLE_MAX_RETRIES")
                or os.getenv("INST_GOOGLE_MAX_RETRIES")
                or os.getenv("AE_GEMINI_MAX_RETRIES")
                or os.getenv("INST_GEMINI_MAX_RETRIES")
            ),
        )

        last_err: Exception | None = None
        total_calls = 0
        total_input_tokens = 0
        total_output_tokens = 0

        for attempt in range(max_retries):
            try:
                text, usage = self._call_text_once(
                    model=model,
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                )
                total_calls += usage.calls
                total_input_tokens += usage.input_tokens
                total_output_tokens += usage.output_tokens
                return text, Usage(
                    calls=total_calls,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                )
            except Exception as e:
                if not _is_transient_google_error(e):
                    raise
                last_err = e
                if attempt == max_retries - 1:
                    break
                time.sleep(0.5 * (2**attempt) + random.random() * 0.2)

        raise RuntimeError(
            f"Google/Gemini transient request failed after {max_retries} attempts: {last_err}"
        ) from last_err

    def call_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[BaseModel],
        temperature: float = 0.0,
        max_output_tokens: int = 1500,
        reasoning_effort: str | None = None,
        text_verbosity: str | None = None,
        max_retries: int = 3,
    ) -> tuple[BaseModel, Usage, str]:
        _ = reasoning_effort, text_verbosity
        max_retries = _resolve_max_retries(
            requested=max_retries,
            env_retries_raw=(
                os.getenv("AE_GOOGLE_MAX_RETRIES")
                or os.getenv("INST_GOOGLE_MAX_RETRIES")
                or os.getenv("AE_GEMINI_MAX_RETRIES")
                or os.getenv("INST_GEMINI_MAX_RETRIES")
            ),
        )
        json_schema = schema.model_json_schema()

        last_err: Exception | None = None
        total_calls = 0
        total_input_tokens = 0
        total_output_tokens = 0

        for attempt in range(max_retries):
            try:
                cfg = types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=float(temperature),
                    max_output_tokens=int(max_output_tokens),
                    response_mime_type="application/json",
                    response_json_schema=json_schema,
                    thinking_config=_thinking_config_for_model(model),
                )
                resp = self._client.models.generate_content(
                    model=model,
                    contents=user,
                    config=cfg,
                )
                text = str(getattr(resp, "text", "") or "").strip()
                parsed_obj = getattr(resp, "parsed", None)
                if parsed_obj is not None:
                    parsed = parsed_obj
                elif text:
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = extract_json_object(text)
                else:
                    raise ValueError("empty response text for JSON call")

                usage = _usage_from_response(resp)
                total_calls += usage.calls
                total_input_tokens += usage.input_tokens
                total_output_tokens += usage.output_tokens
                return (
                    schema.model_validate(parsed),
                    Usage(
                        calls=total_calls,
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                    ),
                    text,
                )
            except Exception as e:
                # Keep older model families usable if schema mode is rejected.
                if (
                    "response_json_schema" in str(e)
                    or "response_mime_type" in str(e)
                    or "thinking mode" in str(e).lower()
                ):
                    text, usage = self.call_text(
                        model=model,
                        system=system,
                        user=user,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        reasoning_effort=reasoning_effort,
                        text_verbosity=text_verbosity,
                        max_retries=max_retries,
                    )
                    parsed = extract_json_object(text)
                    return schema.model_validate(parsed), usage, text
                if not _is_transient_google_error(e):
                    raise
                last_err = e
                if attempt == max_retries - 1:
                    break
                time.sleep(0.5 * (2**attempt) + random.random() * 0.2)

        raise RuntimeError(
            f"Google/Gemini transient request failed after {max_retries} attempts: {last_err}"
        ) from last_err

    def _call_text_once(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_output_tokens: int,
    ) -> tuple[str, Usage]:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=float(temperature),
            max_output_tokens=int(max_output_tokens),
        )
        resp = self._client.models.generate_content(
            model=model,
            contents=user,
            config=cfg,
        )
        text = str(getattr(resp, "text", "") or "").strip()
        if not text:
            text = str(resp)
        return text, _usage_from_response(resp)
