from pathlib import Path

import pytest
from pydantic import ValidationError

from ade_app.config import PipelineConfig
from ade_app.retry import RetryPolicy, is_transient_openai_error


def test_config_rejects_unknown_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("[imaging]\ndpi = 300\nunknown = true\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        PipelineConfig.from_toml(path)


def test_config_loads_strict_toml(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        '[layout]\ndevice = "cpu"\n[retries]\ntransport_max_attempts = 2\n',
        encoding="utf-8",
    )

    config = PipelineConfig.from_toml(path)

    assert config.layout.device == "cpu"
    assert config.retries.transport_max_attempts == 2
    assert config.imaging.dpi == 300


def test_config_deep_merges_model_override(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('[models.luna]\nname = "custom-luna"\n', encoding="utf-8")

    config = PipelineConfig.from_toml(path)

    assert config.models.luna.name == "custom-luna"
    assert str(config.models.luna.input_rate) == "0.20"


def test_retry_policy_bounds_retry_after() -> None:
    class Response:
        def __init__(self) -> None:
            self.headers = {"retry-after": "120"}

    class TransientError(RuntimeError):
        response = Response()

    error = TransientError("transient")
    policy = RetryPolicy(PipelineConfig().retries, random_value=lambda: 0.0)

    assert policy.delay(1, error) == 8.0


def test_status_based_transient_classification() -> None:
    class StatusError(RuntimeError):
        status_code = 503

    assert is_transient_openai_error(StatusError())
    assert not is_transient_openai_error(ValueError("invalid schema"))
