"""ReAct planner for black-box discovery using OpenRouter."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI


DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "done": {"type": "boolean"},
        "hypothesis": {"type": "string"},
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["kind", "title", "content", "confidence"],
            },
        },
        "action": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "method": {"type": "string"},
                "path": {"type": "string"},
                "query": {"type": "string"},
                "body": {"type": "string"},
                "reason": {"type": "string"},
                "auth_mode": {"type": "string", "enum": ["valid", "missing", "invalid"]},
            },
            "required": ["method", "path", "query", "body", "reason", "auth_mode"],
        },
    },
    "required": ["done", "hypothesis", "insights", "action"],
}


@dataclass(frozen=True)
class DiscoveryDecision:
    done: bool
    hypothesis: str
    insights: list[dict[str, Any]]
    action: dict[str, Any]


def _client() -> AsyncOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    return AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        default_headers={
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://frontier.local"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "Frontier Discovery"),
        },
    )


async def decide(
    *,
    base_url: str,
    evidence: list[dict[str, Any]],
    iteration: int,
) -> DiscoveryDecision:
    """Choose one safe next probe after observing the current evidence."""
    compact_evidence = evidence[-80:]
    phases = [
        "map HTTP surface and methods",
        "infer request schema and required fields",
        "infer validation, arity, casing and control rules",
        "infer business rules and numeric boundaries",
        "infer state transitions and resource relationships",
        "test errors, nonexistent identifiers and idempotency",
        "test query parameters, headers and content negotiation",
        "validate the strongest hypotheses with regression probes",
    ]
    prompt = {
        "service_base_url": base_url,
        "iteration": iteration,
        "phase": phases[min(iteration, len(phases) - 1)],
        "evidence": compact_evidence,
        "constraints": {
            "one_action_only": True,
            "query_must_be_a_relative_query_string_or_empty": True,
            "relative_paths_only": True,
            "never_include_authorization_values": True,
            "probe_unknown_routes_methods_status_codes_and_validation": True,
            "the_goal_is_observable_behavior_not_source_code": True,
            "body_must_be_json_encoded_or_empty": True,
            "auth_mode_valid_is_default_but_missing_and_invalid_should_be_tested": True,
            "do_not_repeat_an_existing_request": True,
            "prefer_information_gain_over_generic_requests": True,
            "test_one_hypothesis_per_probe": True,
            "when_a_command_format_is_observed_mutate_one_dimension_only": [
                "missing field", "extra field", "wrong type", "wrong arity",
                "empty string", "null", "boundary number", "negative number",
                "unknown command", "case sensitivity", "invalid control/check digit",
            ],
            "for_stateful_commands": [
                "test a valid transition then an invalid transition",
                "test duplicate operation or idempotency",
                "test nonexistent identifiers",
                "test minimum and maximum amounts",
            ],
        },
    }
    system = (
        "You are the ReAct discovery agent for a legacy black-box HTTP service. "
        "Reason from observations, propose exactly one next HTTP probe, then wait "
        "for its observation. Do not invent hidden implementation details. "
        "The evidence includes the exact request payload and exact response; use both. "
        "Maintain a hypothesis ledger: state the hypothesis, the single variable being "
        "changed, the expected distinguishing outcomes, and why this probe has high information gain. "
        "Do not keep sending generic bodies such as {m:test} or {command:status} after they have "
        "already demonstrated a missing-field error. Prefer mutating a known valid command from "
        "the evidence, testing its boundaries, arity, casing, controls, and state transitions. "
        "The action must use a relative path and can be rejected by the executor. "
        "Include only short, non-duplicated evidence-based insights. Mark done only when no useful "
        "probe remains or the observable contract is sufficiently covered. Never request or repeat secrets."
    )
    client = _client()
    try:
        response = await client.chat.completions.create(
            model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5"),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=1_500,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "discovery_decision",
                    "strict": True,
                    "schema": DISCOVERY_SCHEMA,
                },
            },
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenRouter returned an empty discovery decision")
        data = json.loads(content)
        return DiscoveryDecision(
            done=bool(data["done"]),
            hypothesis=str(data["hypothesis"]),
            insights=list(data["insights"]),
            action=dict(data["action"]),
        )
    finally:
        await client.close()
