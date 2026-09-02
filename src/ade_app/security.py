"""Small security boundary helpers shared by the local UI."""

from __future__ import annotations

import ipaddress


def is_loopback_address(address: str | None) -> bool:
    """Return whether Streamlit is configured to listen only on loopback."""

    if not address:
        return False
    normalized = address.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def public_extraction_error(error: Exception) -> str:
    """Expose actionable configuration failures, but not provider or path details."""

    message = str(error)
    if isinstance(error, RuntimeError) and message.startswith(
        ("OPENAI_", "Required credential", "Calibrated quality profile")
    ):
        return message
    return "Extraction could not be completed. Check the document and app configuration."
