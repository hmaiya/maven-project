"""Field-level cleaners. Pure functions, no I/O, so they are easy to test.

These exist so that on the day you spend your time on the mapping decisions,
not on re-deriving how to parse an epoch timestamp.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

# Timestamps with no zone are assumed to be in this one. Change it if the
# exercise says the data is in another.
DEFAULT_TZ = ZoneInfo("America/New_York")

NULLISH = {"", "n/a", "na", "null", "none", "-", "--", "unknown", "nan"}

_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


# --- text ------------------------------------------------------------------


def clean(value: object) -> str | None:
    """Trim, collapse whitespace, and map every spelling of null to None."""
    if value is None:
        return None
    text = _WS.sub(" ", str(value)).strip()
    return None if text.lower() in NULLISH else text


def strip_html(value: object) -> str:
    """HTML body to plain text."""
    if value is None:
        return ""
    text = re.sub(r"<(br|/p|/div|/li)\s*/?>", " ", str(value), flags=re.I)
    return _WS.sub(" ", html.unescape(_TAG.sub("", text))).strip()


def email(value: object) -> str | None:
    """Lowercased and trimmed, or None if it is not a plausible address."""
    text = clean(value)
    if text is None:
        return None
    text = text.strip("<>").strip().lower()
    return text if _EMAIL.match(text) else None


def email_key(value: object) -> str | None:
    """Match key only, never displayed. Strips +tags, and dots for Gmail.

    liam+support@Tailspin.com -> liam@tailspin.com
    first.last@gmail.com      -> firstlast@gmail.com
    first.last@acme.com       -> first.last@acme.com
    """
    addr = email(value)
    if addr is None:
        return None
    local, _, domain = addr.partition("@")
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
    return f"{local}@{domain}" if local else None


def phone(value: object, default_cc: str = "1") -> str | None:
    """Best-effort E.164. Extensions are dropped."""
    text = clean(value)
    if text is None:
        return None
    text = re.split(r"\b(?:ext|x|extension)\b\.?", text, maxsplit=1, flags=re.I)[0]
    plus = text.strip().startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if not plus and len(digits) == 10:
        return f"+{default_cc}{digits}"
    return f"+{digits}" if 8 <= len(digits) <= 15 else None


def name_key(value: object) -> str | None:
    """Accent-folded, punctuation-free, order-insensitive key for matching.

    "Liam O'Brien" and "OBrien, Liam" both become "liam obrien".
    """
    text = clean(value)
    if text is None:
        return None
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(sorted(re.sub(r"[^a-z0-9\s]", "", folded).split())) or None


def boolean(value: object) -> bool:
    """Source systems spell false as false, FALSE, 0, "" and "no"."""
    text = clean(value)
    return text.lower() in {"true", "t", "yes", "y", "1"} if text else False


# --- timestamps ------------------------------------------------------------


def timestamp(value: object, *, dayfirst: bool = False, tz: ZoneInfo | None = None) -> datetime | None:
    """Anything to aware UTC. Returns None for nullish, raises for garbage.

    Handles epoch seconds, epoch milliseconds, ISO with Z or an offset, naive
    local strings, and slash dates. `dayfirst` picks DD/MM over MM/DD, which
    cannot be inferred from one value, so declare it per source.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value, tz)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _epoch(float(value))

    text = str(value).strip()
    if text.lower() in NULLISH:
        return None
    # "1772000000" is an epoch, not the year 1772.
    if text.isdigit() and len(text) >= 9:
        return _epoch(float(text))
    try:
        parsed = dateparser.parse(text, dayfirst=dayfirst)
    except (ValueError, OverflowError, TypeError) as exc:
        raise ValueError(f"unparseable timestamp: {value!r}") from exc
    if parsed is None:
        raise ValueError(f"unparseable timestamp: {value!r}")
    return _utc(parsed, tz)


def _epoch(raw: float) -> datetime:
    # 1e11 seconds is year 5138 and 1e11 ms is 1973, so the split is safe.
    seconds = raw / 1000.0 if abs(raw) > 1e11 else raw
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"epoch out of range: {raw!r}") from exc


def _utc(dt: datetime, tz: ZoneInfo | None) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or DEFAULT_TZ)
    return dt.astimezone(UTC)


def iso(dt: datetime | None) -> str | None:
    """Storage form: UTC, second precision, trailing Z."""
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- money -----------------------------------------------------------------

MINOR_UNITS = {"JPY": 0, "KRW": 0, "BHD": 3, "KWD": 3}


def money_minor(value: object, currency: str | None = None) -> int | None:
    """Amount as integer minor units (cents). Never store money as float.

    "$1,234.56", "1234.56 USD" and "1.234,56" all give 123456. A value with no
    decimal separator is assumed to already be minor units — confirm that
    assumption against the real data, it is the one that silently breaks.
    """
    text = clean(value)
    if text is None:
        return None
    code = currency or next((c for s, c in
                             {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}.items() if s in text), None)
    if match := re.search(r"\b([A-Z]{3})\b", text.upper()):
        code = code or match.group(1)

    digits = re.sub(r"[^\d.,\-]", "", text)
    if not digits:
        raise ValueError(f"no numeric content: {value!r}")
    exponent = MINOR_UNITS.get((code or "USD").upper(), 2)
    if "." not in digits and "," not in digits:
        return int(digits)
    # The rightmost separator is the decimal point: handles 1,234.56 and 1.234,56.
    if digits.rfind(",") > digits.rfind("."):
        digits = digits.replace(".", "").replace(",", ".")
    else:
        digits = digits.replace(",", "")
    try:
        return int(Decimal(digits).scaleb(exponent).to_integral_value())
    except InvalidOperation as exc:
        raise ValueError(f"unparseable amount: {value!r}") from exc


# --- enums -----------------------------------------------------------------


def mapper(mapping: dict[str, str], default: str = "unknown") -> _Mapper:
    """Build a lookup that remembers values it did not know about.

        status = mapper({"solved": "resolved", "3": "pending"})
        status("Solved")     -> "resolved"
        status("escalated")  -> "unknown"
        status.unmapped      -> {"escalated": 1}

    Watch for inverted scales: a legacy P1 is most urgent, a helpdesk "low" is
    least urgent. Lining them up by position silently inverts urgency.
    """
    return _Mapper({k.lower(): v for k, v in mapping.items()}, default)


class _Mapper:
    def __init__(self, table: dict[str, str], default: str) -> None:
        self.table = table
        self.default = default
        self.unmapped: dict[str, int] = {}

    def __call__(self, value: object) -> str:
        text = clean(value)
        if text is None:
            return self.default
        key = text.lower()
        if key in self.table:
            return self.table[key]
        self.unmapped[key] = self.unmapped.get(key, 0) + 1
        return self.default
