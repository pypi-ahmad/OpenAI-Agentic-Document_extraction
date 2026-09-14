"""Hybrid layout-guided extraction pipeline combining local computer vision and tiered LLM models.

Responsible for orchestrating PP-StructureV3 layout proposals with Luna/Terra/Sol model fallbacks,
applying segment routing decisions, and auditing visual foreground coverage.
Must NOT fabricate missing text or bypass layout confidence thresholds.
Next: ade_app.pipeline where HybridPageExtractor instances process document pages.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Lock

from ade_app.config import PipelineConfig
from ade_app.consensus import PeerEvidence
from ade_app.cost import TokenUsage
from ade_app.coverage import uncovered_foreground
from ade_app.layout import (
    LayoutAnalysis,
    LayoutRegion,
    PPStructureAnalyzer,
    RouteDecision,
    decide_routes,
)
from ade_app.models import (
    AuditedPageExtraction,
    AuditedSemanticPageExtraction,
    ExtractedField,
    SegmentAudit,
    SemanticElement,
    SemanticLeaf,
    SemanticLine,
    SemanticText,
)
from ade_app.openai_client import (
    OpenAIPageExtractor,
    PageResponse,
    PostProcessingResult,
    PrimaryPageResponse,
    _ExtractionState,
    _record_failed_usage,
)
from ade_app.preprocessing import PageTransform, PreparedPage, prepare_page
from ade_app.raster import RenderedPage
from ade_app.rendering import render_semantic_page

LOCAL_PROFILE_PATH = Path("profiles/local-routes.json")


class HybridPageExtractor:
    """Analyze locally first; fail closed to the established Terra/Sol path."""

    def __init__(
        self,
        llm: OpenAIPageExtractor,
        *,
        analyzer: PPStructureAnalyzer | None = None,
        calibrated_routes: set[str] | None = None,
    ) -> None:
        self.capture_primary: Callable[[RenderedPage, PrimaryPageResponse], None] | None = None
        self._llm = llm
        self._analyzer = analyzer or PPStructureAnalyzer()
        self._calibrated_routes = (
            calibrated_routes
            if calibrated_routes is not None
            else _calibrated_routes(config=getattr(llm, "_config", PipelineConfig()))
        )
        self._mode = getattr(llm, "_config", PipelineConfig()).routing.mode
        if self._mode == "baseline":
            self._calibrated_routes = set()
        if self._mode != "selective":
            self._llm._profile_trusted = False
        self._llm.calibrated_routes = self._calibrated_routes
        self._lock = Lock()
        self._decisions: dict[tuple[str, int], RouteDecision] = {}
        self._transforms: dict[tuple[str, int], PageTransform] = {}
        self._regional: dict[tuple[str, int], PreparedPage] = {}

    def analyze_prepared(self, prepared: PreparedPage) -> LayoutAnalysis | None:
        """Analyze a graph-preprocessed page, preserving normal fallback behavior."""

        try:
            return self._analyzer.analyze(prepared)
        except (RuntimeError, ValueError):
            return None

    def resolve_postprocessing_fields(
        self,
        fields: list[ExtractedField],
        pages: tuple[RenderedPage, ...],
        *,
        job_id: str,
    ) -> PostProcessingResult:
        return self._llm.resolve_postprocessing_fields(fields, pages, job_id=job_id)

    def extract_primary(
        self, page: RenderedPage, *, job_id: str, page_count: int
    ) -> PrimaryPageResponse:
        prepared: PreparedPage | None = None
        analysis: LayoutAnalysis | None = None
        try:
            prepared = prepare_page(page)
            analysis = self._analyzer.analyze(prepared)
        except (RuntimeError, ValueError):
            pass
        return self._extract_primary_from_analysis(
            page,
            prepared,
            analysis,
            job_id=job_id,
            page_count=page_count,
        )

    def extract_primary_from_layout(
        self,
        page: RenderedPage,
        prepared: PreparedPage,
        analysis: LayoutAnalysis | None,
        *,
        job_id: str,
        page_count: int,
    ) -> PrimaryPageResponse:
        """Consume graph-owned layout without recomputing preprocessing or detection."""

        return self._extract_primary_from_analysis(
            page,
            prepared,
            analysis,
            job_id=job_id,
            page_count=page_count,
        )

    def _extract_primary_from_analysis(
        self,
        page: RenderedPage,
        prepared: PreparedPage | None,
        analysis: LayoutAnalysis | None,
        *,
        job_id: str,
        page_count: int,
    ) -> PrimaryPageResponse:
        primary = self._extract_primary_impl(
            page, prepared, analysis, job_id=job_id, page_count=page_count
        )
        if self.capture_primary is not None:
            self.capture_primary(page, primary)
        return primary

    def _extract_primary_impl(
        self,
        page: RenderedPage,
        prepared: PreparedPage | None,
        analysis: LayoutAnalysis | None,
        *,
        job_id: str,
        page_count: int,
    ) -> PrimaryPageResponse:
        if self._mode == "baseline":
            return self._llm.extract_primary(page, job_id=job_id, page_count=page_count)
        if prepared is not None and analysis is not None and analysis.status == "complete":
            try:
                missing = uncovered_foreground(
                    prepared.page,
                    (region.prepared_box or region.box for region in analysis.regions),
                )
            except (ValueError, OSError):
                missing = ()
            if missing:
                regions = [*analysis.regions]
                for index, box in enumerate(missing[:16]):
                    regions.append(
                        LayoutRegion(
                            region_id=f"coverage-{index}",
                            kind="other",
                            confidence=0.0,
                            box=prepared.box_to_original(box),
                            prepared_box=box,
                            route="terra",
                        )
                    )
                analysis = analysis.model_copy(update={"regions": regions})
        transform = prepared.transform if prepared is not None else None
        decision = decide_routes(analysis, self._calibrated_routes)
        with self._lock:
            self._decisions[(job_id, page.source_page)] = decision
            if transform is not None:
                self._transforms[(job_id, page.source_page)] = transform

        regional_failure: Exception | None = None
        if (
            prepared is not None
            and analysis is not None
            and analysis.status == "complete"
            and analysis.regions
            and (
                decision.use_full_page_terra
                or any(region.route != "local_text" for region in analysis.regions)
            )
        ):
            extract_regions = getattr(self._llm, "extract_regions", None)
            if extract_regions is not None:
                try:
                    primary = extract_regions(
                        prepared, analysis, job_id=job_id, page_count=page_count
                    )
                except (RuntimeError, ValueError) as error:
                    regional_failure = error
                else:
                    with self._lock:
                        self._regional[(job_id, page.source_page)] = prepared
                    return primary

        if regional_failure is not None:
            try:
                fallback = self._llm.extract_primary(page, job_id=job_id, page_count=page_count)
            except (RuntimeError, ValueError) as error:
                _record_failed_usage(
                    error,
                    getattr(regional_failure, "usage_by_model", ()),
                    int(getattr(regional_failure, "api_call_count", 0)),
                    int(getattr(regional_failure, "retry_count", 0)),
                )
                raise
            return _include_failed_regional_usage(fallback, regional_failure)

        if decision.use_full_page_terra:
            return self._llm.extract_primary(page, job_id=job_id, page_count=page_count)
        if analysis is None or any(region.route != "local_text" for region in analysis.regions):
            return self._llm.extract_primary(page, job_id=job_id, page_count=page_count)
        semantic_children: list[SemanticElement] = []
        audits = []
        scores = []
        for index, region in enumerate(analysis.regions):
            lines = [line.strip() for line in (region.text or "").splitlines() if line.strip()]
            if not lines:
                return self._llm.extract_primary(page, job_id=job_id, page_count=page_count)
            semantic_children.append(
                SemanticLeaf(
                    type="text",
                    box=region.box,
                    lines=[
                        SemanticLine(
                            content=[SemanticText(text=line)], box=region.box, source_kind="printed"
                        )
                        for line in lines
                    ],
                )
            )
            audits.append(
                SegmentAudit(
                    segment_index=index,
                    completeness="complete",
                    image_agreement="supported",
                    findings=[],
                )
            )
            scores.append(round(region.confidence * 100, 2))
        semantic = AuditedSemanticPageExtraction(children=semantic_children, audits=audits)
        rendered = render_semantic_page(semantic)
        state = _ExtractionState(
            semantic=semantic,
            rendered=AuditedPageExtraction(
                markdown=rendered.markdown, children=rendered.children, audits=audits
            ),
        )
        return PrimaryPageResponse(
            state=state,
            usage=TokenUsage(),
            response_id="",
            request_id="",
            service_tier="standard",
            attempts=0,
            source_model="PP-StructureV3",
            source_effort="low",
            route_scores=tuple(scores),
        )

    def finalize(
        self,
        page: RenderedPage,
        primary: PrimaryPageResponse,
        *,
        job_id: str,
        peers: dict[str, PeerEvidence] | None = None,
        allow_sol: bool = True,
    ) -> PageResponse:
        try:
            with self._lock:
                prepared = self._regional.get((job_id, page.source_page))
                decision = self._decisions.get((job_id, page.source_page))
            if prepared is not None and primary.segment_sources:
                result = self._llm.finalize_regions(
                    prepared, primary, job_id=job_id, allow_sol=allow_sol
                )
            else:
                result = self._llm.finalize(
                    page, primary, job_id=job_id, peers=peers, allow_sol=allow_sol
                )
            issues = (
                tuple(issue.code for issue in decision.analysis.issues)
                if decision is not None and decision.analysis is not None
                else ()
            )
            return replace(result, layout_issues=issues)
        finally:
            with self._lock:
                self._decisions.pop((job_id, page.source_page), None)
                self._transforms.pop((job_id, page.source_page), None)
                self._regional.pop((job_id, page.source_page), None)

    def end_document(self, job_id: str) -> None:
        self._llm.end_document(job_id)
        with self._lock:
            for mapping in (self._decisions, self._transforms, self._regional):
                for key in tuple(mapping):
                    if key[0] == job_id:
                        mapping.pop(key, None)

    def extract(self, page: RenderedPage, *, job_id: str, page_count: int) -> PageResponse:
        primary = self.extract_primary(page, job_id=job_id, page_count=page_count)
        return self.finalize(page, primary, job_id=job_id)


def routing_fingerprint(config: PipelineConfig) -> str:
    """Invalidate routing evidence when implementation, models, or inputs change."""
    from importlib.metadata import PackageNotFoundError, version

    from ade_app.openai_client import active_prompt_hashes

    root = Path(__file__).parent
    runtime_versions = {}
    for name in ("paddlepaddle", "paddlepaddle-gpu", "paddlex"):
        try:
            runtime_versions[name] = version(name)
        except PackageNotFoundError:
            runtime_versions[name] = None
    payload = {
        "config": config.model_dump(mode="json"),
        "prompts": active_prompt_hashes(),
        "versions": {
            **runtime_versions,
            **{
                name: version(name)
                for name in ("paddleocr", "opencv-contrib-python", "pymupdf", "openai")
            },
        },
        "code": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "preprocessing.py",
                "layout.py",
                "openai_client.py",
                "fields.py",
                "quality.py",
                "hybrid.py",
                "consensus.py",
                "coverage.py",
                "models.py",
                "rendering.py",
                "pipeline.py",
                "calibration.py",
                "local_calibration.py",
                "config.py",
            )
        },
        "schema": 3,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _calibrated_routes(
    path: Path = LOCAL_PROFILE_PATH, *, config: PipelineConfig | None = None
) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("profile_version") != 2 or payload.get("fingerprint") != routing_fingerprint(
            config or PipelineConfig()
        ):
            return set()
        return {
            name
            for name, result in payload["routes"].items()
            if name in {"local_text", "local_table"}
            and result.get("promotion_passed") is True
            and result.get("accepted_count", 0) > 0
            and result.get("false_accept_count") == 0
            and result.get("held_out_family_count", 0) >= 3
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return set()


def _include_failed_regional_usage(
    fallback: PrimaryPageResponse, error: Exception
) -> PrimaryPageResponse:
    failed = dict(getattr(error, "usage_by_model", ()))
    failed[fallback.source_model] = failed.get(fallback.source_model, TokenUsage()) + fallback.usage
    usage = sum(failed.values(), TokenUsage())
    failed_calls = int(getattr(error, "api_call_count", getattr(error, "attempts", 0)))
    failed_retries = int(getattr(error, "retry_count", 0))
    fallback_calls = fallback.api_call_count or fallback.attempts
    fallback_retries = fallback.retry_count or max(0, fallback.attempts - 1)
    return replace(
        fallback,
        usage=usage,
        attempts=fallback.attempts + failed_calls,
        usage_by_model=tuple(failed.items()),
        api_call_count=fallback_calls + failed_calls,
        retry_count=fallback_retries + failed_retries,
    )
