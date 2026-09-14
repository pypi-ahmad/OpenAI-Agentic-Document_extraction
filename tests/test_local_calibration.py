import pytest

from ade_app.local_calibration import LocalRouteEvidence, LocalRouteSample, local_route_profile

HASH = "a" * 64


def _evidence() -> LocalRouteEvidence:
    return LocalRouteEvidence(
        fingerprint=HASH,
        samples=[
            LocalRouteSample(
                document=name,
                route="local_text",
                crop_sha256=f"{index:064x}",
                value_exact=True,
                critical_values_exact=True,
                field_recall=1.0,
                baseline_field_recall=1.0,
                human_verified=True,
            )
            for index, name in enumerate(("Amerigroup_1", "BadgeCare_1", "Water_1"))
        ],
    )


def test_local_promotion_requires_verified_exact_values_and_paired_recall():
    evidence = _evidence()
    assert local_route_profile(evidence, fingerprint=HASH)["routes"]["local_text"][
        "promotion_passed"
    ]
    evidence.samples[0].critical_values_exact = False
    result = local_route_profile(evidence, fingerprint=HASH)["routes"]["local_text"]
    assert not result["promotion_passed"] and result["false_accept_count"] == 1
    evidence.samples[0].critical_values_exact = True
    evidence.samples[0].field_recall = 0.9
    assert not local_route_profile(evidence, fingerprint=HASH)["routes"]["local_text"][
        "promotion_passed"
    ]


def test_generated_labels_or_related_templates_cannot_promote():
    evidence = _evidence()
    evidence.samples[0].human_verified = False
    assert not local_route_profile(evidence, fingerprint=HASH)["routes"]["local_text"][
        "promotion_passed"
    ]
    evidence.samples[0].human_verified = True
    evidence.samples[1].document = "Amerigroup_2"
    assert not local_route_profile(evidence, fingerprint=HASH)["routes"]["local_text"][
        "promotion_passed"
    ]


def test_stale_or_duplicate_local_evidence_is_rejected():
    evidence = _evidence()
    with pytest.raises(ValueError, match="stale"):
        local_route_profile(evidence, fingerprint="b" * 64)
    evidence.samples.append(evidence.samples[0])
    with pytest.raises(ValueError, match="duplicate"):
        local_route_profile(evidence, fingerprint=HASH)
