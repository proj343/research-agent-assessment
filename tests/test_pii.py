"""Tests for the PII scrubbing module."""

import logging

from agent.pii import scrub


class TestScrubSSN:
    def test_redacts_ssn(self):
        assert scrub("SSN is 123-45-6789 please verify") == "SSN is [SSN] please verify"

    def test_no_false_positive_on_date(self):
        assert scrub("published 2024-01-15") == "published 2024-01-15"

    def test_no_false_positive_on_phone_digits(self):
        # 10-digit runs without SSN dashes should not trigger SSN pattern
        assert "[SSN]" not in scrub("call 8005551234")


class TestScrubCard:
    def test_redacts_card_space_separated(self):
        assert scrub("card 4111 1111 1111 1111 expires") == "card [CARD_NUM] expires"

    def test_redacts_card_dash_separated(self):
        assert scrub("4111-1111-1111-1111") == "[CARD_NUM]"

    def test_no_false_positive_short_number(self):
        assert "[CARD_NUM]" not in scrub("rate is 4.5 percent")


class TestScrubEmail:
    def test_redacts_email(self):
        assert scrub("contact john.doe@example.com for info") == "contact [EMAIL] for info"

    def test_no_false_positive_url(self):
        result = scrub("see fred.stlouisfed.org for data")
        assert "[EMAIL]" not in result


class TestScrubPhone:
    def test_redacts_us_phone_dashes(self):
        assert scrub("call 800-555-1234 now") == "call [PHONE] now"

    def test_redacts_us_phone_parens(self):
        assert scrub("reach us at (800) 555-1234") == "reach us at [PHONE]"

    def test_redacts_us_phone_plus_one(self):
        assert scrub("+1 800 555 1234") == "[PHONE]"

    def test_no_false_positive_year(self):
        assert "[PHONE]" not in scrub("from 2020 to 2024")


class TestScrubAccountNum:
    def test_redacts_labeled_account_number(self):
        result = scrub("account # 123456789 was flagged")
        assert "[ACCOUNT_NUM]" in result

    def test_redacts_acct_prefix(self):
        result = scrub("acct: 987654321 balance")
        assert "[ACCOUNT_NUM]" in result

    def test_no_false_positive_unlabeled_number(self):
        # Bare numbers without "account" prefix should not be redacted
        assert "[ACCOUNT_NUM]" not in scrub("GDP grew by 123456789 dollars")


class TestScrubMultiple:
    def test_redacts_multiple_patterns(self):
        text = "SSN 123-45-6789 email user@bank.com phone 555-867-5309"
        result = scrub(text)
        assert "[SSN]" in result
        assert "[EMAIL]" in result
        assert "[PHONE]" in result

    def test_clean_text_unchanged(self):
        text = "What is the current federal funds rate?"
        assert scrub(text) == text


class TestScrubLogging:
    def test_logs_warning_when_pii_found(self, caplog):
        with caplog.at_level(logging.WARNING, logger="agent.pii"):
            scrub("my SSN is 123-45-6789")
        assert "PII redacted" in caplog.text

    def test_no_log_when_clean(self, caplog):
        with caplog.at_level(logging.WARNING, logger="agent.pii"):
            scrub("What is the GDP of the United States?")
        assert caplog.text == ""
