"""PII scrubbing — redacts sensitive patterns before questions reach the LLM or trace files."""

import logging
import re

logger = logging.getLogger(__name__)

# Each tuple is (compiled pattern, replacement tag).
# Order matters: more specific patterns first to avoid partial overlaps.
_REDACTIONS: list[tuple[re.Pattern, str]] = [
    # Social Security Numbers  e.g. 123-45-6789
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    # Credit/debit card numbers in groups of 4  e.g. 4111 1111 1111 1111
    (re.compile(r"\b(?:\d{4}[- ]){3}\d{4}\b"), "[CARD_NUM]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    # US phone numbers in common formats  e.g. (800) 555-1234 / 800-555-1234 / +1 800 555 1234
    # digit lookbehind/ahead instead of \b so optional leading ( and +1 are included in the match
    (re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"), "[PHONE]"),
    # Bank account numbers when explicitly labeled  e.g. "account # 123456789"
    (re.compile(r"\b(?:account|acct)[\s#:]+\d{6,17}\b", re.IGNORECASE), "[ACCOUNT_NUM]"),
    # API keys in URL query params  e.g. api_key=abc123 or api_key=abc123&next=...
    (re.compile(r"(?i)\bapi_key=[^&\s\"']+"), "api_key=[API_KEY]"),
    # HTTP Authorization headers  e.g. Authorization: Bearer sk-abc123
    (re.compile(r"(?i)\bAuthorization:\s*Bearer\s+\S+"), "Authorization: Bearer [API_KEY]"),
]


def scrub(text: str) -> str:
    """Replace recognized PII and credential patterns with redaction tags."""
    result = text
    found: list[str] = []
    for pattern, tag in _REDACTIONS:
        cleaned = pattern.sub(tag, result)
        if cleaned != result:
            found.append(tag.strip("[]"))
            result = cleaned
    if found:
        logger.warning("PII redacted from input (%s)", ", ".join(found))
    return result


class ScrubFilter(logging.Filter):
    """Logging filter that scrubs PII and API keys from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            cleaned = scrub(msg)
            record.msg = cleaned
            record.args = None
        except Exception:
            pass
        return True
