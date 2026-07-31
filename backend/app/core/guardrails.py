import re

PROMPT_INJECTION_PATTERNS = [
    r"ignore (all )?previous instructions",
    r"disregard (all )?system (prompts|instructions)",
    r"reveal (the )?(system|internal) (prompt|instructions)",
    r"you are now (an? )?admin",
    r"dump (all )?(database|tables|users)",
    r"sql (select|insert|update|delete|drop)",
]

CARD_PATTERN = r"\b(?:\d[ -]*?){13,16}\b"
EMAIL_PATTERN = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+"
URL_PATTERN = r"https?://|www\."
MAX_AI_INPUT_CHARACTERS = 2000


class GuardrailViolation(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Guardrail violation: {reason}")


def validate_and_sanitize_input(text: str) -> str:
    if not text or not text.strip():
        return ""
    if len(text) > MAX_AI_INPUT_CHARACTERS:
        raise GuardrailViolation("oversized_input")

    lowered = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            raise GuardrailViolation("prompt_injection_detected")

    if re.search(CARD_PATTERN, text):
        raise GuardrailViolation("sensitive_data_detected")
    if re.search(URL_PATTERN, text, flags=re.IGNORECASE):
        raise GuardrailViolation("external_link_detected")

    sanitized = re.sub(EMAIL_PATTERN, "[REDACTED_EMAIL]", text)
    return sanitized
