"""In-memory LangGraph workflows for documents and page fan-out.

Responsible for compiling document-level and page-level StateGraph transitions
and conditional edges.
Must NOT configure persistent checkpointers (retaining strict in-memory privacy).
Next: ade_app.orchestration.nodes where each graph step's execution handler is implemented.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from ade_app.orchestration.nodes import (
    dispatch_pages,
    generate_outputs,
    ingest_preprocess,
    layout_analysis,
    next_after_stage,
    route_and_extract,
    run_page,
    validate_and_link,
)
from ade_app.orchestration.state import DocumentState, DocumentWorkflowState
from ade_app.raster import RenderedPage


def build_document_graph() -> Any:
    """Compile authoritative document graph without persistent checkpointing.

    No checkpointer means no resume-from-crash: a crashed run must be retried from
    the start rather than resumed mid-graph. This is deliberate — it also means no
    document content or page images ever sit in a LangGraph checkpoint store,
    consistent with the rest of the app keeping content in memory only.
    """

    graph = StateGraph(DocumentWorkflowState)
    graph.add_node("ingest_preprocess", ingest_preprocess)
    graph.add_node("layout_analysis", layout_analysis)
    graph.add_node("route_and_extract", route_and_extract)
    graph.add_node("validate_and_link", validate_and_link)
    graph.add_node("generate_outputs", generate_outputs)
    graph.add_edge(START, "ingest_preprocess")
    # Each conditional-edge map's keys must match a NextAction value nodes.py can
    # actually return for that stage (see orchestration/state.py's NextAction Literal).
    graph.add_conditional_edges(
        "ingest_preprocess", next_after_stage, {"continue": "layout_analysis", "error": END}
    )
    graph.add_conditional_edges(
        "layout_analysis", next_after_stage, {"continue": "route_and_extract", "error": END}
    )
    graph.add_conditional_edges(
        "route_and_extract",
        next_after_stage,
        {
            "retry": "route_and_extract",
            "validate": "validate_and_link",
            "error": END,
        },
    )
    graph.add_conditional_edges(
        "validate_and_link",
        next_after_stage,
        {"field_retry": "route_and_extract", "generate": "generate_outputs", "error": END},
    )
    graph.add_edge("generate_outputs", END)
    return graph.compile()


# Compiled once at import time and reused for every document in the process, not
# rebuilt per request — StateGraph.compile() is not free and the graph shape is static.
_DOCUMENT_GRAPH = build_document_graph()


def run_document_workflow(state: DocumentWorkflowState) -> DocumentWorkflowState:
    result = _DOCUMENT_GRAPH.invoke(state, config={"recursion_limit": 20})
    return DocumentWorkflowState.model_validate(result)


def _build_page_graph() -> Any:
    graph = StateGraph(DocumentState)
    graph.add_node("run_page", run_page)
    graph.add_conditional_edges(START, dispatch_pages, ["run_page"])
    graph.add_edge("run_page", END)
    return graph.compile()


_PAGE_GRAPH = _build_page_graph()


def run_page_workflow[T](
    pages: tuple[RenderedPage, ...],
    runner: Callable[[RenderedPage], T],
    *,
    max_concurrency: int = 3,
) -> list[T]:
    if not pages:
        return []
    state = _PAGE_GRAPH.invoke(
        DocumentState(pages=pages, page_runner=runner),
        config={"max_concurrency": max_concurrency},
    )
    results = cast(list[T], state["results"])
    # Send-dispatched pages can finish out of order; this requires every runner's
    # result to be an indexable pair whose second element exposes `.source_page`,
    # so the original page order can be restored deterministically here.
    return sorted(results, key=lambda item: cast(Any, item)[1].source_page)
