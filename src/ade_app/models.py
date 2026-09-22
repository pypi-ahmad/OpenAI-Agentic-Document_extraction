"""Parse-only data contract for GPT-6 Sol document conversion."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Box(StrictModel):
    xmin: float = Field(ge=0, le=1)
    ymin: float = Field(ge=0, le=1)
    xmax: float = Field(ge=0, le=1)
    ymax: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> Box:
        if self.xmax <= self.xmin or self.ymax <= self.ymin:
            raise ValueError("box coordinates must describe a positive area")
        return self


BlockType = Literal[
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "chart",
    "diagram",
    "formula",
    "caption",
    "footnote",
    "marginalia",
    "checkbox",
    "attestation",
    "logo",
    "scan_code",
]


class Block(StrictModel):
    id: str = Field(pattern=r"^[a-z_]+-[0-9]+$")
    type: BlockType
    markdown: str = Field(min_length=1)
    box: Box
    description: str | None = None
    asset: str | None = None


class ZoomRequest(StrictModel):
    box: Box
    reason: str = Field(min_length=1, max_length=200)
    rotate_degrees: Literal[0, 90, 180, 270] = 0


class PageRead(StrictModel):
    blocks: list[Block]
    warnings: list[str] = Field(default_factory=list)
    zoom_requests: list[ZoomRequest] = Field(default_factory=list, max_length=4)


class CropRead(StrictModel):
    replacement_blocks: list[Block] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_another_zoom: list[ZoomRequest] = Field(default_factory=list, max_length=4)


class Usage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)
    complete: bool = True

    def plus(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            calls=self.calls + other.calls,
            complete=self.complete and other.complete,
        )


class PageResult(StrictModel):
    page: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    blocks: list[Block]
    warnings: list[str] = Field(default_factory=list)
    status: Literal["ok", "partial", "failed"] = "ok"
    usage: Usage = Field(default_factory=Usage)


class DocumentResult(StrictModel):
    version: Literal["1.0"] = "1.0"
    model: Literal["gpt-6-sol"] = "gpt-6-sol"
    source_filename: str
    pages: list[PageResult]
    markdown: str
    warnings: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
