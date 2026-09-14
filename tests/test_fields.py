from types import SimpleNamespace

from ade_app.fields import (
    _checks,
    _valid_npi,
    apply_field_resolutions,
    build_v2_artifact,
)
from ade_app.models import (
    Billing,
    Box,
    DocumentNode,
    Grounding,
    GroundTruthDocument,
    LeafElement,
    PageNode,
    ParseMetadata,
    TextRange,
)


def _document(markdown: str) -> GroundTruthDocument:
    grounding = Grounding(
        page=1,
        range=TextRange(start=0, end=len(markdown)),
        box=Box(xmin=0, ymin=0, xmax=1, ymax=1),
    )
    return GroundTruthDocument(
        markdown=markdown,
        metadata=ParseMetadata(
            job_id="parse-00000000000000000000000000",
            model_version="test",
            page_count=1,
            output_markdown_chars=len(markdown),
            openapi_spec="test",
            failed_pages=[],
            duration_ms=0,
            billing=Billing(service_tier="standard", total_credits=0),
        ),
        structure=DocumentNode(
            children=[
                PageNode(
                    grounding=grounding,
                    children=[
                        LeafElement(
                            type="text",
                            id="text-0",
                            grounding=grounding,
                            atomic_grounding=[grounding],
                        )
                    ],
                )
            ]
        ),
    )


def test_build_v2_artifact_links_generic_fields() -> None:
    artifact = build_v2_artifact(_document("Member ID: ABC123\nNPI: 1234567893"))

    assert artifact.schema_version == 2
    assert [field.canonical_name for field in artifact.fields] == ["member_id", "npi"]
    assert artifact.fields[1].validation_checks[0].passed


def test_npi_checksum_rejects_invalid_values() -> None:
    assert _valid_npi("1234567893")
    assert not _valid_npi("1234567890")


def test_checkbox_is_boolean_and_inherits_segment_evidence() -> None:
    record = SimpleNamespace(
        source_page=1,
        segments=(SimpleNamespace(final_route="luna", final_score=98.0, reasons=()),),
    )

    artifact = build_v2_artifact(_document("[x] Consent"), (record,))

    assert artifact.fields[0].value is True
    assert artifact.fields[0].confidence == 98.0
    assert artifact.fields[0].evidence[0].route == "luna"


def test_conflict_keeps_best_candidate_and_all_evidence() -> None:
    artifact = build_v2_artifact(_document("Member ID: FIRST\nSubscriber ID: SECOND"))

    field = artifact.fields[0]
    assert field.value == "FIRST"
    assert field.status == "conflict"
    assert field.confidence == 0
    assert [item.candidate for item in field.evidence] == ["FIRST", "SECOND"]


def test_table_alias_and_business_validators() -> None:
    artifact = build_v2_artifact(_document("| Tax ID | 12-3456789 |"))

    assert artifact.fields[0].canonical_name == "tin"
    assert artifact.fields[0].validation_checks[0].passed
    assert not _checks("dob", "2999-01-01")[0].passed


def test_html_table_fields_are_discovered() -> None:
    artifact = build_v2_artifact(
        _document("<table><tr><td>Member ID</td><td>ABC-123</td></tr></table>")
    )

    assert artifact.fields[0].canonical_name == "member_id"
    assert artifact.fields[0].value == "ABC-123"


def test_sol_cannot_replace_an_extracted_value() -> None:
    field = build_v2_artifact(_document("Name: Original Name")).fields[0]

    result = apply_field_resolutions([field], {field.field_id: ("Invented Name", 99.0)})

    assert result[0].value == "Original Name"
    assert result[0].status == "needs_review"
    assert "sol_proposed_new_value_ignored" in result[0].reasons


def test_sol_cannot_choose_one_side_of_a_cross_page_conflict() -> None:
    field = build_v2_artifact(_document("Member ID: FIRST\nSubscriber ID: SECOND")).fields[0]

    result = apply_field_resolutions([field], {field.field_id: ("FIRST", 99.0)})

    assert result[0].status == "conflict"
    assert "sol_confirmation_cannot_resolve_conflict" in result[0].reasons
