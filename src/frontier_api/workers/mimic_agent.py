"""Code-oriented generator used by the mimic worker."""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

MIMIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "service_summary": {"type": "string"},
        "use_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "method": {"type": "string"},
                    "path": {"type": "string"},
                    "behavior": {"type": "string"},
                    "validation": {"type": "array", "items": {"type": "string"}},
                    "response_examples": {"type": "array", "items": {"type": "object"}},
                    "handler_code": {"type": "string"},
                },
                "required": ["name", "method", "path", "behavior", "validation", "response_examples", "handler_code"],
            },
        },
    },
    "required": ["service_summary", "use_cases"],
}


def _client() -> AsyncOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    return AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        default_headers={
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://frontier.local"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "Frontier Mimic"),
        },
    )


async def generate_spec(
    *, service_name: str, base_url: str, insights: list[dict[str, Any]], evidence: list[dict[str, Any]], iteration: int, feedback: str = ""
) -> dict[str, Any]:
    prompt = {
        "service_name": service_name,
        "base_url": base_url,
        "iteration": iteration,
        "previous_replay_feedback": feedback,
        "validated_insights": insights,
        "observed_evidence": evidence[-120:],
        "constraints": [
            "Generate a behavioral contract, not invented implementation details.",
            "Every use case must be traceable to an insight or observed request/response.",
            "Preserve HTTP methods, relative paths, status codes, validation and response shapes.",
            "Do not include secrets, authorization values, or external calls.",
            "The result will later be compiled into isolated handlers backed by mimic_records.",
            "handler_code must define async def handler(req, ctx) and use only req, ctx, Response.",
            "Use await ctx.store.table('name').get/put/delete/list/next_id for state.",
            "Do not import packages, make network calls, access the filesystem, or read environment variables.",
        ],
    }
    client = _client()
    try:
        response = await client.chat.completions.create(
            model=os.getenv("MIMIC_OPENROUTER_MODEL", "openai/gpt-5.6-luna-pro"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior API compatibility engineer. Build a precise, "
                        "evidence-grounded use-case contract for a black-box HTTP service. "
                        "Return only the requested JSON."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=4_000,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "mimic_use_cases", "strict": True, "schema": MIMIC_SCHEMA},
            },
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenRouter returned an empty mimic specification")
        return json.loads(content)
    finally:
        await client.close()
