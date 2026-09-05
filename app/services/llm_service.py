"""
LLM service — the ONLY place in the codebase that talks to the model
provider. Everything else (agent nodes, tools) calls through here.

Provider: Groq, which serves open-source models (Llama 3.3, etc.) over a
hosted API. No model weights are downloaded or run locally — this is a
plain HTTPS API call, same shape as any other LLM provider.

Kept deliberately swappable: if you later want a different open-source
model or provider, only this file changes.
"""
from __future__ import annotations

import json
from typing import Any

from groq import Groq

from app.config import settings

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Get a free key at https://console.groq.com "
                "and add it to backend/.env"
            )
        _client = Groq(api_key=settings.groq_api_key)
    return _client


def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 1500,
    reasoning_effort: str | None = None,
) -> str:
    """
    Single chat completion call. Returns raw text content.

    json_mode=True asks the model to return only a JSON object — used for
    every structured-output call (intent classification, proposed actions).
    Callers must still validate the result against a schema before acting
    on it; json_mode reduces malformed output, it does not guarantee it.

    reasoning_effort ("low" | "medium" | "high") matters specifically for
    reasoning-capable models like openai/gpt-oss-120b: those models spend
    part of max_tokens on internal reasoning before emitting the final
    answer, and default to "medium" effort. For short, structured JSON
    responses this can consume the whole token budget and leave nothing
    for the actual answer — pass reasoning_effort="low" (and a generous
    max_tokens) for those calls. Ignored by models that don't support it.
    """
    client = _get_client()
    kwargs: dict[str, Any] = dict(
        model=settings.groq_model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort

    completion = client.chat.completions.create(**kwargs)
    return completion.choices[0].message.content or ""


def call_llm_json(system_prompt: str, user_prompt: str, **kwargs: Any) -> dict:
    """Convenience wrapper: call_llm in json_mode and parse the result.

    Raises json.JSONDecodeError if the model didn't return valid JSON —
    callers must catch this and handle it as a tool/agent failure, never
    assume success.
    """
    raw = call_llm(system_prompt, user_prompt, json_mode=True, **kwargs)
    return json.loads(raw)