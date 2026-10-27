"""Remove payment card and Social Security numbers from caller text before anything else sees it.

Runs before classification, so card data never reaches the model, the database, or the logs.
"""

import re

_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)")
_SSN_CONTEXT = re.compile(
    r"(?i)\b(?:social(?:\s+security)?|ssn)\b[^\d]{0,20}((?:\d[ -]?){8}\d)(?!\d)"
)

CARD_PLACEHOLDER = "[card number removed]"
SSN_PLACEHOLDER = "[SSN removed]"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def redact(text: str) -> tuple[str, list[str]]:
    """Returns the redacted text and the kinds of data removed."""
    found: list[str] = []

    def card(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found.append("card_number")
            return CARD_PLACEHOLDER
        return match.group(0)

    text = _CARD_CANDIDATE.sub(card, text)

    def ssn_in_context(match: re.Match) -> str:
        found.append("ssn")
        return match.group(0).replace(match.group(1), SSN_PLACEHOLDER)

    text = _SSN_CONTEXT.sub(ssn_in_context, text)

    def ssn(match: re.Match) -> str:
        found.append("ssn")
        return SSN_PLACEHOLDER

    text = _SSN.sub(ssn, text)
    return text, found
