from ade_app.security import is_loopback_address, public_extraction_error


def test_security_boundary() -> None:
    assert is_loopback_address("127.0.0.1")
    assert not is_loopback_address("0.0.0.0")
    assert "secret" not in public_extraction_error(ValueError("secret path"))
