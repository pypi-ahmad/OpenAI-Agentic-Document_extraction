"""Offline promotion of fixed local routes from paired, human-labeled evidence.

Responsible for: validating externally supplied `LocalRouteEvidence` (crop
hashes, exactness/recall flags, human_verified) against a routing fingerprint
and writing `profiles/local-routes.json` only when every sample for a route
passed and was human-verified. Must NOT promote unverified or failing
evidence, and must NOT fit a learned threshold here (see `local_route_profile`
docstring) — this is pass/fail against the fixed production route, unlike the
model-fit profile in `calibration.py`/`quality.py`. The evidence itself is
produced and hand-labeled outside this module.
Next: ade_app.hybrid for route usage and fingerprint generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from ade_app.models import StrictModel
from ade_app.quality import document_family


class LocalRouteSample(StrictModel):
    document: str = Field(min_length=1)
    route: Literal["local_text", "local_table"]
    crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    value_exact: bool
    critical_values_exact: bool
    field_recall: float = Field(ge=0, le=1)
    baseline_field_recall: float = Field(ge=0, le=1)
    human_verified: bool = False


class LocalRouteEvidence(StrictModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    samples: list[LocalRouteSample]


def local_route_profile(evidence: LocalRouteEvidence, *, fingerprint: str) -> dict:
    """No learned threshold: every labeled case must pass the fixed production route."""
    if evidence.fingerprint != fingerprint:
        raise ValueError("local-route evidence fingerprint is stale")
    routes = {}
    for route in ("local_text", "local_table"):
        samples = [sample for sample in evidence.samples if sample.route == route]
        if len({sample.crop_sha256 for sample in samples}) != len(samples):
            raise ValueError("duplicate crops cannot inflate local-route validation")
        families = sorted({document_family(sample.document) for sample in samples})
        false_accepts = sum(
            not sample.value_exact
            or not sample.critical_values_exact
            or sample.field_recall < sample.baseline_field_recall
            for sample in samples
        )
        promoted = (
            bool(samples)
            and len(families) >= 3
            and false_accepts == 0
            and all(sample.human_verified for sample in samples)
        )
        routes[route] = {
            "promotion_passed": promoted,
            "accepted_count": len(samples),
            "false_accept_count": false_accepts,
            "held_out_family_count": len(families),
            "held_out_families": families,
            "human_verified": bool(samples) and all(sample.human_verified for sample in samples),
        }
    return {"profile_version": 2, "fingerprint": fingerprint, "routes": routes}


def calibrate_local_routes(evidence_path: Path, profile_path: Path, *, fingerprint: str) -> Path:
    evidence = LocalRouteEvidence.model_validate_json(evidence_path.read_bytes())
    profile = local_route_profile(evidence, fingerprint=fingerprint)
    promoted = any(route["promotion_passed"] for route in profile["routes"].values())
    destination = profile_path if promoted else profile_path.with_suffix(".candidate.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a concurrent reader (e.g. hybrid.py loading the active
    # profile) never observes a partially written file.
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination
