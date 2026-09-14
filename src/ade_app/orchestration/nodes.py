"""LangGraph nodes with one typed stage hand-off each.

Nodes never raise to the graph: `_execute` catches failures and converts them into
a "next_action": "error" state update instead, so conditional edges can route to END
cleanly rather than the graph invocation throwing mid-run. See graph.py for how
next_action maps to edges, and orchestration/state.py for the WorkflowOperations
Protocol these nodes delegate the real work to.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import cast

from langgraph.types import Send
from openai import OpenAIError

from ade_app.orchestration.state import (
    DocumentState,
    DocumentWorkflowState,
    NextAction,
    PageState,
    StageName,
    WorkflowError,
)
from ade_app.retry import is_transient_openai_error

logger = logging.getLogger(__name__)


def _execute(
    stage: StageName, state: DocumentWorkflowState, operation: Callable[[], Mapping[str, object]]
) -> Mapping[str, object]:
    logger.info("Workflow stage started", extra={"event": "stage_start", "stage": stage})
    try:
        result = operation()
    # Broad by design: any failure from this stage's operation becomes an error state
    # update rather than propagating, so one stage's exception can't crash the graph
    # invocation — route_and_extract below adds a narrower, retryable branch on top.
    except Exception as error:
        logger.exception(
            "Workflow stage failed",
            extra={"event": "stage_error", "stage": stage, "error_code": type(error).__name__},
        )
        return {
            "next_action": "error",
            "errors": [
                WorkflowError(
                    stage=stage,
                    message=f"{type(error).__name__}: {error}",
                    attempt=state.retry_count,
                )
            ],
        }
    logger.info("Workflow stage finished", extra={"event": "stage_end", "stage": stage})
    return result


def ingest_preprocess(state: DocumentWorkflowState) -> Mapping[str, object]:
    return _execute(
        "ingest_preprocess",
        state,
        lambda: {
            "ingestion": state.operations.ingest_preprocess(state.request),
            "next_action": "continue",
        },
    )


def layout_analysis(state: DocumentWorkflowState) -> Mapping[str, object]:
    def run() -> Mapping[str, object]:
        if state.ingestion is None:
            raise RuntimeError("ingest_preprocess produced no hand-off")
        return {
            "layout": state.operations.layout_analysis(state.ingestion),
            "next_action": "continue",
        }

    return _execute("layout_analysis", state, run)


def route_and_extract(state: DocumentWorkflowState) -> Mapping[str, object]:
    def run() -> Mapping[str, object]:
        if state.ingestion is None or state.layout is None:
            raise RuntimeError("layout_analysis produced no hand-off")
        extraction = state.operations.route_and_extract(
            state.layout, state.ingestion, state.extraction, state.retry_plan
        )
        return {
            "extraction": extraction,
            "next_action": "validate",
        }

    # route_and_extract gets its own except clauses (bypassing _execute's generic handler)
    # because only OpenAI errors are worth a graph-level retry, and only when the error
    # looks transient and the retry budget isn't spent; every other failure is terminal.
    try:
        return run()
    except OpenAIError as error:
        attempt = state.retry_count + 1
        if is_transient_openai_error(error) and attempt <= state.config.max_graph_retries:
            return {
                "next_action": "retry",
                "retry_count": attempt,
                "errors": [
                    WorkflowError(
                        stage="route_and_extract",
                        message=f"{type(error).__name__}: transient extraction failure",
                        retryable=True,
                        attempt=attempt,
                    )
                ],
            }
        return {
            "next_action": "error",
            "errors": [
                WorkflowError(
                    stage="route_and_extract",
                    message=f"{type(error).__name__}: extraction request failed",
                    attempt=attempt,
                )
            ],
        }
    except Exception as error:
        logger.exception("Workflow stage failed", extra={"stage": "route_and_extract"})
        return {
            "next_action": "error",
            "errors": [
                WorkflowError(
                    stage="route_and_extract",
                    message=f"{type(error).__name__}: route_and_extract failed",
                    attempt=state.retry_count,
                )
            ],
        }


def validate_and_link(state: DocumentWorkflowState) -> Mapping[str, object]:
    def run() -> Mapping[str, object]:
        if state.extraction is None:
            raise RuntimeError("route_and_extract produced no hand-off")
        validation = state.operations.validate_and_link(state.extraction)
        retry_plan = validation.retry_plan
        if retry_plan is not None and retry_plan.required and state.sol_retry_count == 0:
            return {
                "validation": validation,
                "retry_plan": retry_plan,
                "route_mode": "sol_retry",
                "sol_retry_count": 1,
                "next_action": "sol_retry",
            }
        return {"validation": validation, "retry_plan": None, "next_action": "generate"}

    return _execute("validate_and_link", state, run)


def generate_outputs(state: DocumentWorkflowState) -> Mapping[str, object]:
    def run() -> Mapping[str, object]:
        if state.ingestion is None or state.extraction is None or state.validation is None:
            raise RuntimeError("generate_outputs received an incomplete hand-off")
        return {
            "output": state.operations.generate_outputs(
                state.ingestion, state.extraction, state.validation
            ),
            "next_action": "done",
        }

    return _execute("generate_outputs", state, run)


def next_after_stage(state: DocumentWorkflowState) -> NextAction:
    # Belt-and-suspenders: route_and_extract already checks attempt <= max_graph_retries
    # before ever returning "retry". This guard is a second cap against an infinite
    # retry loop if that invariant is ever violated elsewhere.
    if state.next_action == "retry" and state.retry_count > state.config.max_graph_retries:
        return "error"
    return state.next_action


def dispatch_pages(state: DocumentState) -> list[Send]:
    # Send fans out one isolated run_page invocation per page; results merge back
    # through the operator.add reducer on DocumentState.results (see state.py) rather
    # than each Send needing to know about the others.
    return [
        Send("run_page", {"page": page, "page_runner": state.page_runner, "results": []})
        for page in state.pages
    ]


def run_page(state: PageState | Mapping[str, object]) -> dict[str, list[object]]:
    # LangGraph's Send dispatch delivers a plain dict here, not a validated PageState
    # instance, so both shapes must be handled explicitly.
    if isinstance(state, Mapping):
        runner = state["page_runner"]
        page = state["page"]
        if not callable(runner):
            raise TypeError("page_runner must be callable")
        return {"results": [cast(Callable[[object], object], runner)(page)]}
    return {"results": [state.page_runner(state.page)]}
