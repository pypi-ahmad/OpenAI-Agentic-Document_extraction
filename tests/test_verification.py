from __future__ import annotations

import pytest

from ade_app.verification import valid_npi, verify_sensitive_values


@pytest.mark.parametrize("value", ["1234567893", "1679576722", "1831192848"])
def test_known_valid_npis_pass_checksum(value: str) -> None:
    assert valid_npi(value)
    assert verify_sensitive_values(f"NPI: {value}") == ()


def test_changed_npi_check_digit_is_flagged() -> None:
    assert not valid_npi("1234567894")
    findings = verify_sensitive_values("NPI: 1234567894")

    assert [(finding.code, finding.value) for finding in findings] == [
        ("invalid_npi_checksum", "1234567894")
    ]
