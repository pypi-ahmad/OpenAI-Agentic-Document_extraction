"""Strict, non-secret runtime configuration.

Holds only tunables (model cascade pricing/effort, imaging/layout/routing/retry knobs).
Credentials never live here; they come from the environment or Streamlit secrets
(see openai_client.py's resolve_openai_api_key). Callers needing the resolved
config typically go through cli.py or runner.py, which build a PipelineConfig
from CLI args/TOML and pass it down into the orchestration layer.
"""

from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ade_app.models import StrictModel


class ModelSettings(StrictModel):
    name: str = Field(min_length=1)
    reasoning_effort: Literal["low", "medium", "high", "xhigh"]
    input_rate: Decimal = Field(ge=0)
    cached_input_rate: Decimal = Field(ge=0)
    cache_write_rate: Decimal = Field(ge=0)
    output_rate: Decimal = Field(ge=0)

    @field_validator(
        "input_rate", "cached_input_rate", "cache_write_rate", "output_rate", mode="before"
    )
    @classmethod
    def parse_rate(cls, value: Any) -> Decimal:
        # bool is a subclass of int in Python, so an accidental `true`/`false` in TOML
        # would otherwise coerce to 1/0 and silently pass the `ge=0` price check.
        if isinstance(value, bool):
            raise ValueError("model rates must be non-negative numbers")
        try:
            return Decimal(str(value))
        except Exception as error:
            raise ValueError("model rates must be non-negative numbers") from error


class ModelCascadeSettings(StrictModel):
    # Authoritative price list read by cost.py. Ordering reflects the extraction cascade:
    # luna does primary extraction, terra independently verifies, sol resolves disagreements.
    luna: ModelSettings = ModelSettings(
        name="gpt-5.6-luna",
        reasoning_effort="low",
        input_rate=Decimal("0.20"),
        cached_input_rate=Decimal("0.02"),
        cache_write_rate=Decimal("0.25"),
        output_rate=Decimal("1.20"),
    )
    terra: ModelSettings = ModelSettings(
        name="gpt-5.6-terra",
        reasoning_effort="medium",
        input_rate=Decimal("2.00"),
        cached_input_rate=Decimal("0.20"),
        cache_write_rate=Decimal("2.50"),
        output_rate=Decimal("12.00"),
    )
    sol: ModelSettings = ModelSettings(
        name="gpt-5.6-sol",
        reasoning_effort="low",
        input_rate=Decimal("4.00"),
        cached_input_rate=Decimal("0.40"),
        cache_write_rate=Decimal("5.00"),
        output_rate=Decimal("20.00"),
    )


class ImagingSettings(StrictModel):
    dpi: int = Field(default=300, ge=72, le=600)


class LayoutSettings(StrictModel):
    device: Literal["auto", "gpu", "cpu"] = "auto"
    engine: Literal["PP-StructureV3"] = "PP-StructureV3"
    allow_cpu_fallback: bool = True


class RoutingSettings(StrictModel):
    # baseline: always run the full luna/terra/sol cascade.
    # local_first: skip the cascade for regions the local PP-StructureV3 layout pass
    #   already reads above luna_layout_threshold_percent confidence.
    # selective: local_first, plus route only uncertain segments through terra/sol
    #   (the calibrated fail-closed default; see hybrid.py).
    mode: Literal["baseline", "local_first", "selective"] = "selective"
    luna_layout_threshold_percent: float = Field(default=90.0, ge=0, le=100)
    sol_confidence_threshold_percent: float = Field(default=75.0, ge=0, le=100)
    field_review_threshold_percent: float = Field(default=75.0, ge=0, le=100)
    quality_threshold_percent: float = Field(default=90.0, ge=0, le=100)
    max_escalated_segments_per_page: int = Field(default=16, ge=0, le=64)
    max_sol_fields_per_document: int = Field(default=8, ge=0, le=64)


class RetrySettings(StrictModel):
    transport_max_attempts: int = Field(default=3, ge=1, le=5)
    structured_output_max_attempts: int = Field(default=2, ge=1, le=3)
    graph_max_page_retries: int = Field(default=1, ge=0, le=2)
    backoff_initial_seconds: float = Field(default=0.5, ge=0, le=10)
    backoff_max_seconds: float = Field(default=8.0, ge=0, le=60)
    jitter_seconds: float = Field(default=0.25, ge=0, le=5)

    @model_validator(mode="after")
    def validate_backoff(self) -> RetrySettings:
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise ValueError("retry backoff maximum must be at least the initial delay")
        return self


class RuntimeSettings(StrictModel):
    openai_timeout_seconds: float = Field(default=300.0, gt=0, le=900)
    max_page_workers: int = Field(default=3, ge=1, le=16)
    max_api_concurrency: int = Field(default=4, ge=1, le=32)


class LoggingSettings(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["text", "json"] = "text"


class PipelineConfig(StrictModel):
    models: ModelCascadeSettings = ModelCascadeSettings()
    imaging: ImagingSettings = ImagingSettings()
    layout: LayoutSettings = LayoutSettings()
    routing: RoutingSettings = RoutingSettings()
    retries: RetrySettings = RetrySettings()
    runtime: RuntimeSettings = RuntimeSettings()
    logging: LoggingSettings = LoggingSettings()

    @classmethod
    def from_toml(cls, path: str | Path) -> PipelineConfig:
        config_path = Path(path)
        try:
            with config_path.open("rb") as stream:
                payload = tomllib.load(stream)
        # Only file-read/parse failures are remapped here. A TOML file that parses fine but
        # has the wrong shape (e.g. a string where a number belongs) fails later, inside
        # model_validate, as a pydantic ValidationError instead of this ValueError.
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"configuration could not be read: {config_path}") from error
        defaults = cls().model_dump()
        # TOML overrides are a partial patch onto the defaults, not a full replacement:
        # an omitted key (or omitted table) keeps its default rather than becoming empty.
        return cls.model_validate(_deep_merge(defaults, payload))


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in update.items():
        current = result.get(key)
        result[key] = (
            _deep_merge(current, value)
            if isinstance(current, dict) and isinstance(value, dict)
            else value
        )
    return result
