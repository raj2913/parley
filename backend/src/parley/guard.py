"""Output guard: the last check before any model-written text reaches a caller.

It is deliberately dumb and deterministic. It does not judge tone or helpfulness (the eval
suite does that); it answers one question: does this text say anything the agent is not
allowed to say right now?
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from parley.offers import CENT

_SMALL = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SCALE = {"hundred": 100, "thousand": 1000}
_NUMBER_WORD = r"(?:" + "|".join([*_SMALL, *_SCALE, "and", "a"]) + r")"
_WORD_SEQUENCE = rf"{_NUMBER_WORD}(?:[\s-]+{_NUMBER_WORD})*"

_DIGIT_AMOUNT = re.compile(r"\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)")
_DIGIT_DOLLARS = re.compile(
    r"\b((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)\s*(?:dollars?|bucks|usd)\b", re.I
)
_WORD_DOLLARS = re.compile(
    rf"\b({_WORD_SEQUENCE})\s+dollars?(?:\s+and\s+({_WORD_SEQUENCE})\s+cents?)?\b", re.I
)
_COUNT = re.compile(
    rf"\b(\d+|{_WORD_SEQUENCE})[\s-]+(?:more\s+|equal\s+|monthly\s+)*"
    r"(?:payments?|installments?|months?)\b",
    re.I,
)
_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH_DAY = re.compile(
    r"\b("
    + "|".join(sorted(_MONTHS, key=len, reverse=True))
    + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.I,
)
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%|\bpercent\b|\bper\s*cent\b", re.I)
_MARKDOWN = re.compile(r"(\*\*|__|`|^#+\s|^\s*[-*•]\s+)", re.M)


def words_to_number(phrase: str) -> int | None:
    total, current, seen = 0, 0, False
    for token in re.split(r"[\s-]+", phrase.lower().strip()):
        if token in ("and", ""):
            continue
        if token == "a":
            current = current or 1
            continue
        if token in _SMALL:
            current += _SMALL[token]
            seen = True
        elif token == "hundred":
            current = (current or 1) * 100
            seen = True
        elif token == "thousand":
            total += (current or 1) * 1000
            current = 0
            seen = True
        else:
            return None
    return total + current if seen else None


def extract_amounts(text: str) -> list[Decimal]:
    amounts: list[Decimal] = []
    for match in _DIGIT_AMOUNT.finditer(text):
        amounts.append(Decimal(match.group(1).replace(",", "")).quantize(CENT))
    for match in _DIGIT_DOLLARS.finditer(text):
        amounts.append(Decimal(match.group(1).replace(",", "")).quantize(CENT))
    for match in _WORD_DOLLARS.finditer(text):
        dollars = words_to_number(match.group(1))
        if dollars is None:
            continue
        cents = words_to_number(match.group(2)) if match.group(2) else 0
        amounts.append((Decimal(dollars) + Decimal(cents or 0) / 100).quantize(CENT))
    return amounts


def extract_counts(text: str) -> list[int]:
    counts = []
    for match in _COUNT.finditer(text):
        token = match.group(1)
        value = int(token) if token.isdigit() else words_to_number(token)
        if value is not None:
            counts.append(value)
    return counts


_SPLIT_DECIMAL = re.compile(r"(\d)\s+\.(\d{2})\b")


def extract_dates(text: str) -> list[tuple[int, int]]:
    """(month, day) pairs for dates written like 'November 26th' or 'Nov 26'."""
    dates = []
    for match in _MONTH_DAY.finditer(text):
        day = int(match.group(2))
        if 1 <= day <= 31:
            dates.append((_MONTHS[match.group(1).lower()], day))
    return dates


def sanitize_for_speech(text: str) -> str:
    """Strip markdown a voice channel would read aloud, rejoin split decimals, collapse spaces."""
    text = " ".join(_MARKDOWN.sub("", text).split())
    return _SPLIT_DECIMAL.sub(r"\1.\2", text)


@dataclass
class GuardContext:
    verified: bool
    allowed_amounts: set[Decimal]
    allowed_counts: set[int]
    prohibited_phrases: list[str]
    third_party_sensitive_terms: list[str]
    creditor_name: str
    agency_name: str = ""
    # (month, day) pairs the agent may say; None skips the date check.
    allowed_dates: set[tuple[int, int]] | None = None
    max_words: int = 120


@dataclass
class GuardResult:
    text: str
    violations: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _phrase_pattern(phrase: str) -> re.Pattern:
    return re.compile(r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b", re.I)


def check_output(raw_text: str, ctx: GuardContext) -> GuardResult:
    text = sanitize_for_speech(raw_text)
    result = GuardResult(text=text)

    def flag(rule: str, detail: str) -> None:
        result.violations.append({"rule": rule, "detail": detail})

    if not text:
        flag("empty", "no text to say")
        return result

    for phrase in ctx.prohibited_phrases:
        if _phrase_pattern(phrase).search(text):
            flag("prohibited_language", f"contains '{phrase}'")

    amounts = extract_amounts(text)
    if not ctx.verified:
        # Before identity verification nothing about the account may be said.
        if amounts:
            flag("pre_verification_disclosure", "mentions a dollar amount")
        for term in ctx.third_party_sensitive_terms:
            if _phrase_pattern(term).search(text):
                flag("pre_verification_disclosure", f"mentions '{term}'")
        if _phrase_pattern(ctx.creditor_name).search(text):
            flag("pre_verification_disclosure", "names the creditor")
        if ctx.agency_name and _phrase_pattern(ctx.agency_name).search(text):
            flag("pre_verification_disclosure", "names the collection agency")
    else:
        for amount in amounts:
            if amount not in ctx.allowed_amounts:
                flag("unapproved_amount", f"${amount:,} was not approved by a propose_* tool")
        for count in extract_counts(text):
            if count not in ctx.allowed_counts:
                flag("unapproved_term", f"{count} payments/months was not approved")
        if ctx.allowed_dates is not None:
            for month, day in extract_dates(text):
                if (month, day) not in ctx.allowed_dates:
                    flag("unapproved_date", f"date {month}/{day} is not the approved payment date")

    if _PERCENT.search(text):
        flag("percentage", "talk in dollar amounts, never percentages")

    if len(text.split()) > ctx.max_words:
        flag("too_long", f"{len(text.split())} words; keep spoken turns short")

    return result
