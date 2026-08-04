"""Prompt-building helpers for structured LLM exchanges.

The model is constrained to return JSON that validates into a Pydantic model; these
helpers render the JSON schema and the parsing system instruction. Detailed,
stage-specific prompts (parsing, extraction, repair) live with their own modules and
compose these primitives.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

_PARSE_SYSTEM = (
    "You are a quantitative-research assistant. Read the user's research idea and "
    "respond with ONLY a single JSON object that exactly matches the supplied schema. "
    "Do not include prose, explanations, or markdown fences. If a value is genuinely "
    "unknown, omit the field or set it to null rather than inventing one."
)


def build_schema_instruction(response_model: type[BaseModel]) -> str:
    """Render the system instruction carrying the target JSON schema."""
    schema = response_model.model_json_schema()
    return (
        f"{_PARSE_SYSTEM}\n\n"
        f"Respond with JSON matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


def build_system_prompt(instruction: str) -> str:
    """Wrap a stage-specific instruction with the global parsing rules."""
    return f"{_PARSE_SYSTEM}\n\n{instruction}"


__all__ = ["build_schema_instruction", "build_system_prompt"]
