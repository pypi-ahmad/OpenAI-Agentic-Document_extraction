from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ade_app.config import PipelineConfig
from ade_app.models import SemanticCheckbox
from ade_app.spending import BudgetedResponses, SpendingStopped, SpendLedger


def test_schema_failure_retains_known_provider_usage(tmp_path):
    ledger = SpendLedger(Decimal("1"), tmp_path / "spending.json")
    responses = Mock()
    responses.input_tokens.count.return_value = SimpleNamespace(input_tokens=100)
    raw = responses.with_raw_response.parse.return_value
    raw.http_response.json.return_value = {"usage": {"input_tokens": 100, "output_tokens": 20}}
    raw.parse.side_effect = ValueError("invalid extraction schema")
    guarded = BudgetedResponses(responses, ledger, PipelineConfig())
    with pytest.raises(ValueError, match="invalid extraction schema"):
        guarded.parse(
            model="gpt-6-sol", input=[], text_format=SemanticCheckbox, max_output_tokens=100
        )
    assert ledger.snapshot()["unknown_calls"] == 0
    assert Decimal(str(ledger.snapshot()["charged_usd"])) == Decimal("0.000495")
    assert ledger.snapshot()["pending_calls"] == 0
    ledger.close()


def test_unknown_request_stays_reserved_after_resume(tmp_path):
    path = tmp_path / "spending.json"
    ledger = SpendLedger(Decimal("1"), path)
    ledger.reserve(Decimal("0.8"))
    with pytest.raises(ValueError, match="already in use"):
        SpendLedger(Decimal("1"), path)
    ledger.close()
    resumed = SpendLedger(Decimal("1"), path)
    assert resumed.snapshot()["charged_usd"] == "0.8"
    assert resumed.snapshot()["unknown_calls"] == 1
    with pytest.raises(SpendingStopped):
        resumed.reserve(Decimal("0.3"))
    resumed.close()


def test_reservation_releases_only_known_savings(tmp_path):
    ledger = SpendLedger(Decimal("1"), tmp_path / "spending.json")
    ticket = ledger.reserve(Decimal("0.9"))
    ledger.settle(ticket, Decimal("0.2"))
    ticket = ledger.reserve(Decimal("0.8"))
    ledger.settle(ticket, None)
    assert ledger.snapshot()["charged_usd"] == "1.0"
    with pytest.raises(SpendingStopped):
        ledger.reserve(Decimal("0.01"))
    ledger.close()


def test_preflight_failure_never_dispatches_model_request(tmp_path):
    ledger = SpendLedger(Decimal("10"), tmp_path / "spending.json")
    responses = Mock()
    responses.input_tokens.count.side_effect = RuntimeError("unavailable")
    guarded = BudgetedResponses(responses, ledger, PipelineConfig())
    with pytest.raises(SpendingStopped, match="preflight"):
        guarded.parse(
            model="gpt-6-sol", input=[], text_format=SemanticCheckbox, max_output_tokens=100
        )
    responses.parse.assert_not_called()
    assert ledger.calls == 0
    ledger.close()


def test_zero_configured_rates_cannot_bypass_budget(tmp_path):
    ledger = SpendLedger(Decimal("0.01"), tmp_path / "spending.json")
    responses = Mock()
    responses.input_tokens.count.return_value = SimpleNamespace(input_tokens=500_000)
    config = PipelineConfig()
    config.model.input_rate = config.model.output_rate = Decimal(0)
    config.model.cache_write_rate = Decimal(0)
    guarded = BudgetedResponses(responses, ledger, config)
    with pytest.raises(SpendingStopped):
        guarded.parse(
            model="gpt-6-sol", input=[], text_format=SemanticCheckbox, max_output_tokens=100
        )
    responses.parse.assert_not_called()
    ledger.close()
