from types import SimpleNamespace

from ade_app.config import ParserConfig
from ade_app.inputs import DocumentInput
from ade_app.models import Block, Box, CropRead, PageRead, ZoomRequest
from ade_app.parser import (
    CROP_MAX_OUTPUT_TOKENS,
    MARKDOWN_MAX_OUTPUT_TOKENS,
    PAGE_MAX_OUTPUT_TOKENS,
    parse_document,
)
from ade_app.prompts import load_prompt


class FakeResponses:
    def __init__(self) -> None:
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            parsed = PageRead(
                blocks=[
                    Block(
                        id="paragraph-0",
                        type="paragraph",
                        markdown="Old",
                        box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
                    )
                ],
                zoom_requests=[
                    ZoomRequest(box=Box(xmin=0, ymin=0, xmax=1, ymax=1), reason="small")
                ],
            )
        else:
            parsed = CropRead(
                replacement_blocks=[
                    Block(
                        id="paragraph-0",
                        type="paragraph",
                        markdown="Exact",
                        box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
                    )
                ]
            )
        usage = SimpleNamespace(
            input_tokens=10, output_tokens=5, input_tokens_details=None, output_tokens_details=None
        )
        return SimpleNamespace(output_parsed=parsed, usage=usage)


def test_parser_uses_only_sol_and_bounded_adaptive_read(monkeypatch, png_bytes: bytes) -> None:
    fake = FakeResponses()
    monkeypatch.setattr("ade_app.parser.build_client", lambda api_key, config: fake)
    document, assets, _ = parse_document(
        DocumentInput("scan.png", png_bytes),
        (1,),
        api_key="test",
        config=ParserConfig(max_zoom_rounds=2, max_crops_per_page=4),
    )
    assert document.markdown == "Exact\n"
    assert document.usage.calls == 2
    assert document.usage.complete is True
    assert assets == {}
    assert all(call["model"] == "gpt-6-sol" and call["store"] is False for call in fake.calls)
    assert fake.calls[0]["instructions"] == load_prompt("page_parse.md")
    assert fake.calls[1]["instructions"] == load_prompt("crop_read.md")
    assert fake.calls[0]["max_output_tokens"] == PAGE_MAX_OUTPUT_TOKENS
    assert fake.calls[1]["max_output_tokens"] == CROP_MAX_OUTPUT_TOKENS
    assert "page 1 of 1" in fake.calls[0]["input"][0]["content"][0]["text"]
    assert "Crop box" in fake.calls[1]["input"][0]["content"][0]["text"]
    assert all(
        image["detail"] == "original"
        for call in fake.calls
        for item in call["input"]
        for image in item["content"]
        if image["type"] == "input_image"
    )


class RetryResponses(FakeResponses):
    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            PageRead.model_validate_json('{"blocks":')
        usage = SimpleNamespace(
            input_tokens=10, output_tokens=5, input_tokens_details=None, output_tokens_details=None
        )
        return SimpleNamespace(output_parsed=PageRead(blocks=[]), usage=usage)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(
            input_tokens=10, output_tokens=5, input_tokens_details=None, output_tokens_details=None
        )
        return SimpleNamespace(output_text="# Recovered", usage=usage)


def test_parser_falls_back_to_grounded_markdown(monkeypatch, png_bytes: bytes) -> None:
    fake = RetryResponses()
    monkeypatch.setattr("ade_app.parser.build_client", lambda api_key, config: fake)

    document, _, _ = parse_document(DocumentInput("scan.png", png_bytes), (1,), api_key="test")

    assert len(fake.calls) == 2
    assert document.pages[0].status == "partial"
    assert document.usage.calls == 2
    assert document.usage.complete is False
    assert document.markdown == "# Recovered\n"
    assert "recovered as layout-aware Markdown" in document.warnings[0]
    assert fake.calls[1]["instructions"] == load_prompt("page_markdown.md")
    assert fake.calls[1]["max_output_tokens"] == MARKDOWN_MAX_OUTPUT_TOKENS


class FailedResponses(FakeResponses):
    def parse(self, **kwargs):
        self.calls.append(kwargs)
        PageRead.model_validate_json('{"blocks":')

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text="", usage=None)


def test_parser_keeps_failed_page_in_document(monkeypatch, png_bytes: bytes) -> None:
    fake = FailedResponses()
    monkeypatch.setattr("ade_app.parser.build_client", lambda api_key, config: fake)

    document, _, _ = parse_document(DocumentInput("scan.png", png_bytes), (1,), api_key="test")

    assert len(fake.calls) == 2
    assert document.pages[0].status == "failed"
    assert document.pages[0].blocks == []
    assert document.usage.calls == 2
    assert document.usage.complete is False
    assert "incomplete structured model output after 2 attempts" in document.warnings[0]
