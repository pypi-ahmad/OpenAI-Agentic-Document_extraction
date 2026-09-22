"""Small, fixed configuration for the single-model parser."""

from decimal import Decimal

from pydantic import Field

from ade_app.models import StrictModel


class ParserConfig(StrictModel):
    model: str = Field(default="gpt-6-sol", pattern="^gpt-6-sol$")
    reasoning_effort: str = Field(default="medium", pattern="^medium$")
    dpi: int = Field(default=300, ge=150, le=400)
    max_zoom_rounds: int = Field(default=2, ge=0, le=2)
    max_crops_per_page: int = Field(default=4, ge=0, le=4)
    timeout_seconds: float = Field(default=300, gt=0, le=900)
    input_rate: Decimal = Decimal("2.00")
    cached_input_rate: Decimal = Decimal("0.20")
    cache_write_rate: Decimal = Decimal("2.50")
    output_rate: Decimal = Decimal("10.00")
