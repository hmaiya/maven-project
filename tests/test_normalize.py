"""Tests for the cleaners. Add a case here the moment real data surprises you."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from unify import normalize as n


class TestTimestamps:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1772000000, datetime(2026, 2, 25, 6, 13, 20, tzinfo=UTC)),
            (1772000000000, datetime(2026, 2, 25, 6, 13, 20, tzinfo=UTC)),   # millis
            ("1772000000", datetime(2026, 2, 25, 6, 13, 20, tzinfo=UTC)),
            ("2026-03-05T14:22:00Z", datetime(2026, 3, 5, 14, 22, tzinfo=UTC)),
            ("2026-03-05T09:22:00-05:00", datetime(2026, 3, 5, 14, 22, tzinfo=UTC)),
        ],
    )
    def test_every_format_lands_on_utc(self, value: object, expected: datetime) -> None:
        assert n.timestamp(value) == expected

    def test_naive_uses_the_declared_default_zone(self) -> None:
        assert n.timestamp("2026-03-05 09:22:00") == datetime(
            2026, 3, 5, 9, 22, tzinfo=n.DEFAULT_TZ
        ).astimezone(UTC)

    def test_dayfirst_disambiguates_slash_dates(self) -> None:
        assert n.timestamp("03/05/2026").month == 3
        assert n.timestamp("03/05/2026", dayfirst=True).month == 5

    @pytest.mark.parametrize("value", ["", "  ", "N/A", "null", "-", None])
    def test_nullish_is_none(self, value: object) -> None:
        assert n.timestamp(value) is None

    def test_garbage_raises_so_the_caller_can_reject_the_row(self) -> None:
        with pytest.raises(ValueError):
            n.timestamp("not-a-date")

    def test_iso_is_utc_with_z(self) -> None:
        assert n.iso(datetime(2026, 3, 5, 14, 22, 3, 500, tzinfo=UTC)) == "2026-03-05T14:22:03Z"


class TestEmailAndPhone:
    def test_email_is_lowercased_and_trimmed(self) -> None:
        assert n.email("  Maria.G@Example.COM ") == "maria.g@example.com"

    @pytest.mark.parametrize("value", ["not an email", "a@b", "", "N/A", None])
    def test_junk_email_is_rejected(self, value: object) -> None:
        assert n.email(value) is None

    def test_email_key_strips_plus_tags_and_gmail_dots(self) -> None:
        assert n.email_key("Liam+support@Tailspin.com") == "liam@tailspin.com"
        assert n.email_key("first.last@gmail.com") == "firstlast@gmail.com"
        assert n.email_key("first.last@acme.com") == "first.last@acme.com"

    @pytest.mark.parametrize(
        "value", ["+1 (617) 555-0142", "617-555-0142", "617.555.0142", "6175550142", "617-555-0142 ext. 9"]
    )
    def test_us_phone_formats_collapse_to_one_value(self, value: str) -> None:
        assert n.phone(value) == "+16175550142"

    def test_international_phone_is_preserved(self) -> None:
        assert n.phone("+81 3-5555-0123") == "+81355550123"


class TestText:
    @pytest.mark.parametrize("value", ["", " ", "N/A", "null", "None", "-", "unknown"])
    def test_every_spelling_of_null_becomes_none(self, value: str) -> None:
        assert n.clean(value) is None

    def test_whitespace_is_collapsed(self) -> None:
        assert n.clean("  two   words \n") == "two words"

    def test_html_becomes_readable_text(self) -> None:
        assert n.strip_html("<div><p>Hi&nbsp;there</p><br/><p>a &amp; b</p></div>") == "Hi there a & b"

    def test_name_key_folds_accents_punctuation_and_order(self) -> None:
        assert n.name_key("Liam O'Brien") == n.name_key("OBrien, Liam") == "liam obrien"
        assert n.name_key("Chloé Dubois") == "chloe dubois"

    def test_boolean_handles_every_spelling_of_false(self) -> None:
        assert n.boolean("TRUE") and n.boolean("1") and n.boolean("yes")
        assert not n.boolean("false") and not n.boolean("0") and not n.boolean(None)


class TestMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("$1,234.56", 123456), ("1234.56 USD", 123456), ("123456", 123456), ("1.234,56 EUR", 123456)],
    )
    def test_every_format_gives_the_same_minor_units(self, value: str, expected: int) -> None:
        assert n.money_minor(value) == expected

    def test_zero_decimal_currencies_are_not_multiplied(self) -> None:
        assert n.money_minor("1,234.00", "JPY") == 1234

    def test_nullish_is_none_and_junk_raises(self) -> None:
        assert n.money_minor("N/A") is None
        with pytest.raises(ValueError):
            n.money_minor("free")


class TestMapper:
    def test_lookup_is_case_insensitive_and_records_misses(self) -> None:
        status = n.mapper({"solved": "resolved", "3": "pending"})
        assert status("Solved") == "resolved"
        assert status(" 3 ") == "pending"
        assert status("escalated") == "unknown"
        assert status.unmapped == {"escalated": 1}

    def test_nullish_gets_the_default_without_being_flagged(self) -> None:
        status = n.mapper({"open": "open"})
        assert status("N/A") == "unknown"
        assert status.unmapped == {}
