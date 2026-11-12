"""Turn raw values into comparable values and report format problems. Raw values are never overwritten."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from .schema import CField

PLACEHOLDERS = {"", "n/a", "na", "null", "none", "-", "--", "unknown", "tbd", "?", "nan"}
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC AB BC MB NB NL NS ON PE QC SK".split())
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


@dataclass
class NormResult:
    norm: str | None
    ok: bool = True
    msg: str = ""


def is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:
        return True
    return str(v).strip().lower() in PLACEHOLDERS


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def name_key(s: str) -> str:
    s = _strip_accents(str(s)).strip()
    if "," in s:  # "Smith, John" -> "John Smith"
        last, first = s.split(",", 1)
        s = f"{first} {last}"
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def normalize(f: CField, raw: Any) -> NormResult:
    if is_blank(raw):
        return NormResult(None)
    s = str(raw).strip()
    t = f.dtype
    if t == "name":
        n = name_key(s)
        return NormResult(n, len(n.split()) >= 2, "" if len(n.split()) >= 2 else "Only one name part found")
    if t == "phone":
        d = re.sub(r"\D", "", s)
        if len(d) == 11 and d.startswith("1"):
            d = d[1:]
        return NormResult(d, len(d) == 10, "" if len(d) == 10 else f"Expected 10 digits, found {len(d)}")
    if t == "email":
        e = s.lower()
        return NormResult(e, bool(EMAIL_RE.match(e)), "" if EMAIL_RE.match(e) else "Not a valid email address")
    if t == "vin":
        v = re.sub(r"[^A-Za-z0-9]", "", s).upper()
        ok = bool(VIN_RE.match(v))
        return NormResult(v, ok, "" if ok else f"VIN must be 17 characters (no I, O, Q); found {len(v)}")
    if t == "license":
        v = re.sub(r"[^A-Za-z0-9]", "", s).upper()
        return NormResult(v, len(v) >= 5, "" if len(v) >= 5 else "License number looks too short")
    if t == "state":
        v = s.upper()
        return NormResult(v, v in US_STATES, "" if v in US_STATES else "Not a US/Canada state or province code")
    if t == "date":
        try:
            if re.fullmatch(r"\d{5}(\.\d+)?", s):  # Excel serial date
                d = pd.to_datetime(float(s), unit="D", origin="1899-12-30")
            else:
                d = pd.to_datetime(s, errors="raise")
            if not (1950 <= d.year <= 2100):
                return NormResult(None, False, "Date is outside 1950-2100")
            return NormResult(d.date().isoformat())
        except Exception:  # noqa: BLE001
            return NormResult(None, False, "Unrecognized date format")
    if t == "year":
        try:
            y = int(float(s))
        except ValueError:
            return NormResult(None, False, "Year is not a number")
        ok = 1980 <= y <= date.today().year + 1
        return NormResult(str(y), ok, "" if ok else "Year is out of range")
    if t == "enum":
        v = re.sub(r"\s+", " ", s.lower())
        ok = not f.allowed or v in f.allowed
        return NormResult(v, ok, "" if ok else f"Not one of: {', '.join(f.allowed)}")
    if t == "id":
        v = re.sub(r"\s+", "", s).upper()
        return NormResult(v)
    if t == "plate":
        v = re.sub(r"[^A-Za-z0-9]", "", s).upper()
        return NormResult(v, 2 <= len(v) <= 8, "" if 2 <= len(v) <= 8 else "Plate length looks wrong")
    return NormResult(re.sub(r"\s+", " ", s).casefold())
