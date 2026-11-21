"""Flags cell text that reads like an instruction to an AI system.

This is a visibility aid, not the defence. The defence is structural: file
content is only ever handled as data, the LLM only returns schema-validated
suggestions drawn from an allowlist, and nothing the LLM says can trigger a
write. This scan exists so an operator can *see* an attempt in the audit log.
"""

from __future__ import annotations

import re

MIN_LENGTH = 8

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b.{0,40}?\b(previous|prior|above|earlier|all|any|your)\b"
            r".{0,40}?\b(instructions?|prompts?|rules?|guidelines?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    ("role_prefix", re.compile(r"^\s*(system|assistant|developer)\s*:", re.IGNORECASE | re.MULTILINE)),
    ("role_tag", re.compile(r"</?\s*(system|assistant|user|instructions?|prompt)\s*>", re.IGNORECASE)),
    ("persona_override", re.compile(r"\byou are now\b|\bfrom now on,? you\b", re.IGNORECASE)),
    ("prompt_reference", re.compile(r"\b(system prompt|jailbreak|prompt injection)\b", re.IGNORECASE)),
    (
        "action_request",
        re.compile(
            r"\b(auto-?approve|approve|skip( the)? (review|validation))\b.{0,30}?"
            r"\b(mapping|import|records?|validation)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)


def match(value: str) -> str | None:
    """Return the name of the first rule the text trips, or None."""
    if len(value) < MIN_LENGTH:
        return None
    for name, pattern in _RULES:
        if pattern.search(value):
            return name
    return None


def excerpt(value: str, limit: int = 80) -> str:
    flat = " ".join(value.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
