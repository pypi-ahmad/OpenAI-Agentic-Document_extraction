"""Persisted reservations for bounded evaluation spending.

Responsible for thread-safe and cross-process budget reservation (`SpendLedger`),
enforcing hard financial spend caps before API dispatch, and recovering ledger
state across restarts.
Must NOT persist raw document content, prompt texts, or credentials in ledger files.
Next: ade_app.evaluation which installs a spend ledger during evaluation suite runs.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from threading import Condition
from typing import Any

from openai import pydantic_function_tool

from ade_app.config import PipelineConfig
from ade_app.cost import TokenUsage


class SpendingStopped(ValueError):
    """No model request was dispatched."""

    attempts = 0
    api_call_count = 0
    retry_count = 0
    usage = TokenUsage()


class SpendLedger:
    """Reserve before dispatch; unknown outcomes remain charged across restarts."""

    def __init__(self, limit: Decimal, path: Path) -> None:
        if not limit.is_finite() or limit <= 0:
            raise ValueError("budget must be finite and positive")
        self.path = path
        self.limit = limit
        self.spent = Decimal(0)
        self.pending: dict[int, Decimal] = {}
        self.calls = 0
        self.unknown_calls = 0
        self._condition = Condition()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = path.with_suffix(".lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._lock_file.seek(0)
                if self._lock_file.read(1) == b"":
                    self._lock_file.write(b"0")
                    self._lock_file.flush()
                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise ValueError("spending ledger is already in use") from None
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if Decimal(saved["limit_usd"]) != limit:
                raise ValueError("resumed budget cannot change its limit")
            self.spent = Decimal(saved["charged_usd"])
            self.calls = int(saved["calls"])
            self.unknown_calls = int(saved["unknown_calls"]) + int(saved["pending_calls"])
            if not self.spent.is_finite() or self.spent < 0:
                raise ValueError("invalid spending ledger")

    def reserve(self, maximum: Decimal) -> int:
        if not maximum.is_finite() or maximum <= 0:
            raise SpendingStopped("request cost bound unavailable")
        with self._condition:
            while self.spent + sum(self.pending.values()) + maximum > self.limit:
                if not self.pending or self.spent + maximum > self.limit:
                    raise SpendingStopped("evaluation budget cannot cover the next request")
                self._condition.wait(timeout=1)
            self.calls += 1
            ticket = self.calls
            self.pending[ticket] = maximum
            self._save()
            return ticket

    def settle(self, ticket: int, actual: Decimal | None) -> None:
        with self._condition:
            if actual is not None and (not actual.is_finite() or actual < 0):
                actual = None
            reserved = self.pending.pop(ticket)
            if actual is None:
                self.unknown_calls += 1
            self.spent += reserved if actual is None else actual
            self._save()
            self._condition.notify_all()
            if actual is not None and actual > reserved:
                # Freeze the ledger rather than risking another underestimated request.
                self.spent = max(self.limit, self.spent)
                self._save()
                raise SpendingStopped("provider usage exceeded the reserved bound")

    def close(self) -> None:
        self._lock_file.close()

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            return {
                "limit_usd": str(self.limit),
                "charged_usd": str(self.spent + sum(self.pending.values())),
                "calls": self.calls,
                "pending_calls": len(self.pending),
                "unknown_calls": self.unknown_calls,
            }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.snapshot(), indent=2), encoding="utf-8")
        temporary.replace(self.path)


class BudgetedResponses:
    """Use the provider's token counter, then reserve full output at uncached rates."""

    def __init__(self, responses: Any, ledger: SpendLedger, config: PipelineConfig) -> None:
        self.responses = responses
        self.ledger = ledger
        self.models = {
            m.name: m for m in (config.models.luna, config.models.terra, config.models.sol)
        }

    def parse(self, **kwargs: Any) -> Any:
        model = kwargs["model"]
        if model not in {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"}:
            raise SpendingStopped("verified cost rules unavailable for configured model")
        count_args = {
            key: kwargs[key]
            for key in ("model", "input", "instructions", "reasoning")
            if key in kwargs
        }
        tool = pydantic_function_tool(kwargs["text_format"])["function"]
        count_args["text"] = {
            "format": {
                "type": "json_schema",
                "name": tool["name"],
                "strict": True,
                "schema": tool["parameters"],
            }
        }
        try:
            count = self.responses.input_tokens.count(**count_args).input_tokens
        except Exception as error:
            raise SpendingStopped("provider input-token preflight unavailable") from error
        if type(count) is not int or not 0 <= count <= 922_000:
            raise SpendingStopped("invalid input-token preflight")
        settings = self.models[model]
        defaults = PipelineConfig().models
        verified = next(m for m in (defaults.luna, defaults.terra, defaults.sol) if m.name == model)
        long_context = count > 272_000
        # Include cache-write premium and the documented regional endpoint uplift.
        input_rate = max(
            settings.input_rate,
            settings.cache_write_rate,
            verified.input_rate,
            verified.cache_write_rate,
        ) * (2 if long_context else 1)
        output_rate = max(settings.output_rate, verified.output_rate) * (
            Decimal("1.5") if long_context else 1
        )
        output_limit = kwargs["max_output_tokens"]
        if type(output_limit) is not int or not 1 <= output_limit <= 128_000:
            raise SpendingStopped("bounded output limit required")
        maximum = (
            (Decimal(count + 1) * input_rate + Decimal(output_limit) * output_rate)
            / 1_000_000
            * Decimal("1.1")
        )
        ticket = self.ledger.reserve(maximum)
        actual = None
        try:
            raw = self.responses.with_raw_response.parse(**{**kwargs, "service_tier": "default"})
            payload = raw.http_response.json()
            usage = payload.get("usage") if isinstance(payload, dict) else None
            if (
                isinstance(usage, dict)
                and type(usage.get("input_tokens")) is int
                and type(usage.get("output_tokens")) is int
                and usage["input_tokens"] >= 0
                and usage["output_tokens"] >= 0
            ):
                actual = (
                    (
                        Decimal(usage["input_tokens"]) * input_rate
                        + Decimal(usage["output_tokens"]) * output_rate
                    )
                    / 1_000_000
                    * Decimal("1.1")
                )
            # Read usage before schema parsing: a malformed extraction still has
            # a known provider charge and must not lose its usage metadata.
            response = raw.parse()
        except BaseException:
            self.ledger.settle(ticket, actual)
            raise
        self.ledger.settle(ticket, actual)
        return response
