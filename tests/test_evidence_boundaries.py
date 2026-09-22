import io
from decimal import Decimal
from types import SimpleNamespace

from PIL import Image, ImageDraw

from ade_app.cost import TokenUsage, calculate_cost
from ade_app.coverage import uncovered_foreground
from ade_app.fields import _found_fields
from ade_app.models import Box, SegmentPatchAudit
from ade_app.openai_client import _read_usage
from ade_app.quality import document_family
from ade_app.raster import RenderedPage


def test_patch_schema_requires_zero_audit_index():
    from ade_app.models import SemanticSegmentPatchBatch

    schema = SemanticSegmentPatchBatch.model_json_schema()
    index = schema["$defs"]["SegmentPatchAudit"]["properties"]["segment_index"]
    assert index["const"] == 0


def test_blank_label_cannot_consume_next_line():
    fields = _found_fields("Name:\nMember ID: ABC123\nChoice: [?]\n")
    assert [(name, value) for _, name, value in fields] == [
        ("Name", ""),
        ("Member ID", "ABC123"),
        ("Choice", "[?]"),
    ]


def test_html_field_evidence_points_at_value_cell():
    html = "<tr><td>Member ID</td><td>ABC123</td></tr>"
    offset, name, value = _found_fields(html)[0]
    assert html[offset:].startswith("ABC123")
    assert (name, value) == ("Member ID", "ABC123")


def test_coverage_finds_omission_and_ignores_blank_page():
    image = Image.new("RGB", (200, 200), "white")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    assert uncovered_foreground(RenderedPage(1, stream.getvalue(), 200, 200), ()) == ()
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "Member ID", fill="black")
    draw.text((20, 100), "Missing value", fill="black")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    page = RenderedPage(1, stream.getvalue(), 200, 200)
    missing = uncovered_foreground(page, [Box(xmin=0, ymin=0, xmax=1, ymax=0.4)])
    assert missing and all(box.ymin > 0.4 for box in missing)
    assert uncovered_foreground(page, [Box(xmin=0, ymin=0, xmax=1, ymax=1)]) == ()


def test_long_context_billing_is_per_request_not_aggregate():
    small = TokenUsage(input_tokens=150_000, output_tokens=100)
    assert calculate_cost(small + small) == 2 * calculate_cost(small)
    long = _read_usage(
        SimpleNamespace(usage=SimpleNamespace(input_tokens=300_000, output_tokens=100))
    )
    expected = (Decimal(300_000) * 4 + Decimal(100) * 15) / 1_000_000
    assert calculate_cost(long) == expected
    assert calculate_cost(long + small) == expected + calculate_cost(small)


def test_related_document_variants_share_held_out_family():
    assert document_family("Masked Amerigroup_1") == document_family(
        "Masked_Amerigroup_RealSolutions_2"
    )
    assert document_family("BadgeCare_1") != document_family("PublicWaterMassMailing")


def test_verified_reads_reuse_only_within_job(monkeypatch, audited_semantic_page):
    from ade_app.models import AuditedPageExtraction, SemanticSegmentPatch
    from ade_app.openai_client import OpenAIPageExtractor, _ExtractionState
    from ade_app.rendering import render_semantic_page

    rendered = render_semantic_page(audited_semantic_page)
    state = _ExtractionState(
        audited_semantic_page,
        AuditedPageExtraction(
            markdown=rendered.markdown,
            children=rendered.children,
            audits=audited_semantic_page.audits,
        ),
    )
    patch = SemanticSegmentPatch(
        segment_id="p1-s0",
        element=audited_semantic_page.children[0],
        audit=SegmentPatchAudit.model_validate(audited_semantic_page.audits[0].model_dump()),
    )
    extractor = OpenAIPageExtractor(object(), profile_path=None)
    calls = []

    def read(*args, **kwargs):
        calls.append(kwargs["job_id"])
        return patch, TokenUsage(input_tokens=10), "response", "request", "default", 1

    monkeypatch.setattr(extractor, "_independent_read_uncached", read)
    page = RenderedPage(1, b"pixels", 100, 100)
    first = extractor._independent_read(page, state, 0, job_id="one", batch_index=1, peer=None)
    reused = extractor._independent_read(page, state, 0, job_id="one", batch_index=2, peer=None)
    assert first[1].input_tokens == 10 and reused[1] == TokenUsage() and reused[5] == 0
    extractor._independent_read(page, state, 0, job_id="two", batch_index=1, peer=None)
    extractor.end_document("one")
    extractor._independent_read(page, state, 0, job_id="one", batch_index=1, peer=None)
    assert calls == ["one", "two", "one"]
