from parley.redact import CARD_PLACEHOLDER, SSN_PLACEHOLDER, redact


def test_card_numbers_are_removed_only_when_they_pass_luhn():
    text, found = redact("sure my card is 4111 1111 1111 1111 exp 04/29")
    assert CARD_PLACEHOLDER in text and "4111" not in text and found == ["card_number"]
    # A 16-digit number that fails Luhn (e.g. a reference) is left alone.
    assert redact("reference 1234567812345678")[1] == []


def test_ssns_are_removed():
    text, found = redact("it's 123-45-6789")
    assert SSN_PLACEHOLDER in text and found == ["ssn"]
    text, found = redact("my social is 123456789")
    assert "123456789" not in text and found == ["ssn"]


def test_ordinary_numbers_survive():
    text, found = redact("I can pay $155 on 11/01/2026, zip 94110, born 03/14/1985")
    assert found == [] and "94110" in text
