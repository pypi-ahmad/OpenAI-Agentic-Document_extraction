"""Non-correcting checks for error-prone identifiers and numeric fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    code: str
    value: str


_NPI = re.compile(r"\bNPI\s*:\s*(\d{10})\b", re.IGNORECASE)
_DATE = re.compile(r"\b(?:date|DOB)\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", re.IGNORECASE)
_PHONE = re.compile(r"\b(?:phone|fax)\s*:\s*([+]?[\d(). -]{7,20}\d)\b", re.IGNORECASE)
_ICD = re.compile(r"\bICD-10 code\(s\)\s*:\s*([^\n<]+)", re.IGNORECASE)
_CPT = re.compile(r"\bCPT code\(s\)[^:]*:\s*([^\n<]+)", re.IGNORECASE)


def verify_sensitive_values(text: str) -> tuple[VerificationFinding, ...]:
    """Flag unsupported formats; never alter extracted values."""

    findings: list[VerificationFinding] = []
    for value in _NPI.findall(text):
        if not valid_npi(value):
            findings.append(VerificationFinding("invalid_npi_checksum", value))
    for value in _DATE.findall(text):
        if not _valid_date(value):
            findings.append(VerificationFinding("invalid_date", value))
    for value in _PHONE.findall(text):
        digits = re.sub(r"\D", "", value)
        if not 7 <= len(digits) <= 15:
            findings.append(VerificationFinding("invalid_phone_or_fax", value.strip()))
    for value in _ICD.findall(text):
        for code in _codes(value):
            if not re.fullmatch(r"[A-TV-Z]\d{2}(?:\.\w{1,4})?", code, re.IGNORECASE):
                findings.append(VerificationFinding("invalid_icd10_format", code))
    for value in _CPT.findall(text):
        for code in _codes(value):
            if not re.fullmatch(r"(?:\d{5}|[A-Z]\d{4})", code, re.IGNORECASE):
                findings.append(VerificationFinding("invalid_cpt_format", code))
    return tuple(findings)


def valid_npi(value: str) -> bool:
    if not re.fullmatch(r"\d{10}", value):
        return False
    digits = [int(digit) for digit in "80840" + value[:-1]]
    total = 0
    for index, digit in enumerate(digits):
        product = digit * (2 if index % 2 == 1 else 1)
        total += product // 10 + product % 10
    return (10 - total % 10) % 10 == int(value[-1])


def _valid_date(value: str) -> bool:
    for pattern in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            datetime.strptime(value, pattern)
            return True
        except ValueError:
            pass
    return False


def _codes(value: str) -> tuple[str, ...]:
    return tuple(part.strip(" .;:") for part in value.split(",") if part.strip(" .;:"))
