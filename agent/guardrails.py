"""Input guardrails — validates questions before they reach the LLM."""

import logging
import re

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2000

# Each tuple is (compiled pattern, short label for the warning log).
_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "ignore [all] [previous/your] instructions/rules/constraints"
    (
        re.compile(
            r"\bignore\s+(all\s+)?(previous|prior|above|your)\s+"
            r"(instructions?|prompts?|rules?|constraints?)\b",
            re.I,
        ),
        "instruction override",
    ),
    # "forget / disregard [all] [the/your] [previous/prior] instructions"
    (
        re.compile(
            r"\b(forget|disregard)\s+(?:all\s+)?(?:(?:the|your)\s+)?(?:(?:previous|prior)\s+)?"
            r"(instructions?|rules?|constraints?|guidelines?)\b",
            re.I,
        ),
        "instruction override",
    ),
    # "you are now [X]" — persona switch
    (re.compile(r"\byou\s+are\s+now\b", re.I), "persona switch"),
    # "pretend you are / pretend to be"
    (re.compile(r"\bpretend\s+(you\s+are|to\s+be)\b", re.I), "persona switch"),
    # "new instructions:" or "new system instructions:"
    (re.compile(r"\bnew\s+(system\s+)?instructions?\s*:", re.I), "instruction injection"),
    # Common LLM prompt-template delimiters smuggled in user input
    (
        re.compile(r"<\|(?:system|im_start|endoftext)\|>|\[INST\]|<<SYS>>", re.I),
        "template injection",
    ),
    # Explicit jailbreak keyword
    (re.compile(r"\bjailbreak\b", re.I), "jailbreak"),
]

_REFUSAL = (
    "This question cannot be processed. "
    "Please ask a finance, banking, or economics research question."
)


class GuardrailError(ValueError):
    """Raised when a question fails an input guardrail check."""


def validate(question: str) -> None:
    """Raise GuardrailError if the question is too long or contains injection patterns."""
    if len(question) > MAX_QUESTION_CHARS:
        raise GuardrailError(
            f"Question exceeds the {MAX_QUESTION_CHARS}-character limit "
            f"({len(question)} chars). Please ask a more concise question."
        )

    for pattern, label in _INJECTION_PATTERNS:
        if pattern.search(question):
            logger.warning("Guardrail triggered: %s", label)
            raise GuardrailError(_REFUSAL)
