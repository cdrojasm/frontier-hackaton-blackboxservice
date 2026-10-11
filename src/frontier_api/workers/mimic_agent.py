"""Code-oriented generator used by the mimic worker."""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

USE_CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "method": {"type": "string"},
        "path": {"type": "string"},
        "behavior": {"type": "string"},
        "validation": {"type": "array", "items": {"type": "string"}},
        "response_examples": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "integer"},
                    # JSON-encode arbitrary response bodies so the
                    # strict provider schema remains closed.
                    "body": {"type": "string"},
                },
                "required": ["status", "body"],
            },
        },
        "handler_code": {"type": "string"},
    },
    "required": ["name", "method", "path", "behavior", "validation", "response_examples", "handler_code"],
}

HANDLER_CONSTRAINTS = [
    "Generate exactly one use case for target_endpoint; keep its method and path template unchanged.",
    "Every behavior must be traceable to an insight or observed request/response.",
    "Preserve status codes, validation and exact response shapes, field names and value formats.",
    "response_examples.body must be a JSON-encoded string, never a nested object.",
    "handler_code must define async def handler(req, ctx) and use only req, ctx, Response.",
    "req has method, path, query, body (decoded JSON) and params (path parameters).",
    "Return Response(status, body) where body is a JSON-serializable value, not a JSON string.",
    "Use await ctx.store.table('name').get/put/delete/list/next_id for state.",
    "Follow state_model exactly (table names, keys, fields, ID formats) so handlers of other endpoints interoperate.",
    ("STATE STARTS EMPTY: at the start of every discovery run (field `run` in observed_evidence) the original was "
     "reset, so there are no records and every ID counter starts at 1. Never seed, preload or hardcode records, "
     "counters, balances or IDs in handler code; a missing record must produce the not-found behavior."),
    ("Replay runs every observed request in chronological order against ONE shared store: "
    "IDs, counters and balances accumulate across sequences exactly as in observed_evidence."),
    "Do not hardcode observed response values; derive them from state and the request.",
    "Do not import packages, make network calls, access the filesystem, or read environment variables.",
]


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


MAX_TOKENS = int(os.getenv("MIMIC_MAX_TOKENS", "16000"))


async def generate_handler(
    *,
    shared_context: dict[str, Any],
    endpoint: dict[str, Any],
    examples: list[dict[str, Any]],
    previous: dict[str, Any] | None = None,
    failures: list[dict[str, Any]] | None = None,
    other_handlers: dict[str, str] | None = None,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Generate (or repair) the handler of a single endpoint.

    shared_context is identical for every call of a run and goes first so the
    provider can reuse its prompt cache; the endpoint-specific part goes last.
    """
    task: dict[str, Any] = {"target_endpoint": endpoint, "endpoint_examples": examples}
    if other_handlers:
        task["other_handlers"] = other_handlers
    if previous is not None:
        task["current_handler"] = previous
        task["failing_replays"] = failures or []
        task["instructions"] = (
            "Repair current_handler so failing_replays match. A failure may come from state that "
            "another endpoint writes; align with state_model and other_handlers instead of special-casing."
        )
    client = _client()
    try:
        response = await client.chat.completions.create(
            model=os.getenv("MIMIC_OPENROUTER_MODEL", "openai/gpt-5.6-luna-pro"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior API compatibility engineer. Write the handler of one endpoint "
                        "of a black-box HTTP service so it reproduces observed traffic exactly. "
                        "Return only the requested JSON.\nConstraints:\n- " + "\n- ".join(HANDLER_CONSTRAINTS)
                    ),
                },
                {"role": "user", "content": json.dumps(shared_context, ensure_ascii=False)},
                {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
            ],
            temperature=temperature,
            max_tokens=MAX_TOKENS,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "mimic_use_case", "strict": True, "schema": USE_CASE_SCHEMA},
            },
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenRouter returned an empty mimic handler")
        use_case = json.loads(content)
        use_case["method"] = str(endpoint["method"]).upper()
        use_case["path"] = endpoint["path"]
        return use_case
    finally:
        await client.close()


async def generate_pipeline(
    *, service_name: str, base_url: str, insights: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    """Generate the design, rules and tasks planning phases in a single model call."""
    output_shape = {
        "design": {
            "summary": "string",
            "entities": ["string"],
            "endpoints": [{"method": "string", "path": "string", "purpose": "string"}],
            "state_models": ["string"],
            "verbs": [{"verb": "string", "behavior": "string"}],
            "state_model": {
                "tables": [{
                    "name": "string",
                    "key": "string",
                    "fields": [{"name": "string", "type": "string", "notes": "string"}],
                    "id_format": "string",
                    "written_by": ["METHOD /path"],
                    "read_by": ["METHOD /path"],
                }],
            },
        },
        "rules": {"rules": [{"id": "string", "statement": "string", "evidence_refs": ["string"], "applies_to": ["string"]}]},
        "tasks": {"tasks": [{"id": "string", "title": "string", "description": "string", "rule_refs": ["string"], "acceptance_criteria": ["string"]}]},
    }
    prompt = {
        "output_shape": output_shape,
        "service_name": service_name,
        "base_url": base_url,
        "insights": insights,
        "observed_evidence": evidence[-120:],
        "instructions": [
            "Return only valid JSON matching output_shape with the keys design, rules and tasks.",
            "Build design first, then rules referencing design, then tasks referencing rule IDs.",
            "Keep every item evidence-grounded; do not invent undocumented behavior.",
            "design.endpoints must list every distinct observed route once, using {param} templates for variable segments.",
            ("design.state_model is the shared storage contract every handler will follow: one table per entity, "
            "ID formats (e.g. AC-%04d from a next_id counter), and fields such as balances that other endpoints update."),
            "State persists across sequences (ID counters, balances, status transitions).",
            ("State starts EMPTY at the beginning of each discovery run (field `run`): no records, counters at 1. "
             "Never plan seed data or preloaded records."),
            "Use stable IDs so later phases can trace rules and tasks back to evidence.",
        ],
    }
    client = _client()
    try:
        response = await client.chat.completions.create(
            model=os.getenv("MIMIC_OPENROUTER_MODEL", "openai/gpt-5.6-luna-pro"),
            messages=[
                {"role": "system", "content": "You are a senior API reverse-engineering planner. Return only valid JSON."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=MAX_TOKENS,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Empty response for mimic planning pipeline")
        result = json.loads(content)
        return {phase: result.get(phase, {}) for phase in output_shape}
    finally:
        await client.close()
