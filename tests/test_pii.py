"""Tests for the PII scrubbing module."""

import logging

from agent.pii import ScrubFilter, scrub


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


class TestScrubApiKey:
    def test_redacts_api_key_query_param(self):
        url = "https://api.stlouisfed.org/fred/series?series_id=UNRATE&api_key=realkey123&file_type=json"
        result = scrub(url)
        assert "realkey123" not in result
        assert "api_key=[API_KEY]" in result

    def test_api_key_redaction_case_insensitive(self):
        result = scrub("request failed: API_KEY=MySecret123")
        assert "MySecret123" not in result

    def test_api_key_stops_at_ampersand(self):
        url = "https://example.com?api_key=secret&other=value"
        result = scrub(url)
        assert "other=value" in result

    def test_redacts_authorization_bearer(self):
        header = "Authorization: Bearer sk-abc123xyz"
        result = scrub(header)
        assert "sk-abc123xyz" not in result
        assert "Authorization: Bearer [API_KEY]" in result

    def test_authorization_redaction_case_insensitive(self):
        result = scrub("authorization: bearer MyToken999")
        assert "MyToken999" not in result

    def test_clean_url_without_api_key_unchanged(self):
        url = "https://en.wikipedia.org/wiki/Federal_Reserve"
        assert scrub(url) == url


class TestScrubLogging:
    def test_logs_warning_when_pii_found(self, caplog):
        with caplog.at_level(logging.WARNING, logger="agent.pii"):
            scrub("my SSN is 123-45-6789")
        assert "PII redacted" in caplog.text

    def test_no_log_when_clean(self, caplog):
        with caplog.at_level(logging.WARNING, logger="agent.pii"):
            scrub("What is the GDP of the United States?")
        assert caplog.text == ""


class TestScrubFilter:
    def _make_record(self, msg: str, *args) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg=msg,
            args=args,
            exc_info=None,
        )
        return record

    def test_filter_always_returns_true(self):
        f = ScrubFilter()
        record = self._make_record("clean message")
        assert f.filter(record) is True

    def test_filter_scrubs_pii_from_message(self):
        f = ScrubFilter()
        record = self._make_record("user SSN is 123-45-6789")
        f.filter(record)
        assert "123-45-6789" not in record.msg
        assert "[SSN]" in record.msg

    def test_filter_scrubs_api_key_from_message(self):
        f = ScrubFilter()
        record = self._make_record("request failed: https://api.example.com?api_key=secret99&x=1")
        f.filter(record)
        assert "secret99" not in record.msg
        assert "api_key=[API_KEY]" in record.msg

    def test_filter_formats_args_before_scrubbing(self):
        f = ScrubFilter()
        record = self._make_record("url: %s", "https://api.example.com?api_key=tok123")
        f.filter(record)
        assert "tok123" not in record.msg
        assert record.args is None

    def test_filter_leaves_clean_message_unchanged(self):
        f = ScrubFilter()
        record = self._make_record("step 1 completed in 120ms")
        f.filter(record)
        assert record.msg == "step 1 completed in 120ms"

    def test_filter_survives_malformed_format_string(self):
        f = ScrubFilter()
        record = self._make_record("bad format %s %s", "only_one_arg")
        assert f.filter(record) is True  # must not raise
