import json
from dataclasses import asdict

import pytest

from minicode.observability import Prices, RunRecorder, safe_config
from minicode.providers import ModelConfig


def prices():
    return Prices("fixture", "USD", "2026-11-05", 2, 8, .2, 3)


@pytest.mark.parametrize("raw,normalized,expected", [
    ({"prompt_tokens": 100, "prompt_cache_hit_tokens": 40, "completion_tokens": 10,
      "completion_tokens_details": {"reasoning_tokens": 8}},
     {"input_tokens": 100, "output_tokens": 10}, (60 * 2 + 40 * .2 + 10 * 8) / 1_000_000),
    ({"input_tokens": 60, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 10,
      "output_tokens": 10},
     {"input_tokens": 100, "output_tokens": 10}, (60 * 2 + 30 * .2 + 10 * 3 + 10 * 8) / 1_000_000),
    ({"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40}, "output_tokens": 10},
     {"input_tokens": 100, "output_tokens": 10}, (60 * 2 + 40 * .2 + 10 * 8) / 1_000_000),
])
def test_price_accounting_does_not_double_count_cache_or_reasoning(raw, normalized, expected):
    recorder = RunRecorder(prices=prices())
    event = {"type": "request_end", "local_request_id": "one", "usage": raw, "normalized_usage": normalized}
    recorder.record(event)
    recorder.record(event)
    summary = recorder.summary()
    assert summary["requests"] == 1
    assert summary["estimated_cost"] == pytest.approx(expected)
    assert summary["observed_usage"] == normalized


def test_missing_usage_is_unknown_and_keeps_known_subtotal():
    recorder = RunRecorder(prices=prices())
    recorder.record({"type": "request_end", "usage": {"input_tokens": 100, "output_tokens": 10},
                     "normalized_usage": {"input_tokens": 100, "output_tokens": 10}})
    recorder.record({"type": "request_end", "usage": None, "normalized_usage": None, "status": "error"})
    result = recorder.summary()
    assert result["estimated_cost"] is None
    assert result["unknown_usage_requests"] == 1
    assert result["known_cost_subtotal"] > 0


def test_metadata_log_is_exclusive_and_preserves_agent_attribution(tmp_path):
    path = tmp_path / "events.jsonl"
    recorder = RunRecorder(path)
    recorder.record({"type": "agent_start", "agent_id": "main"})
    recorder.record({"type": "agent_start", "agent_id": "child", "parent_id": "main"})
    recorder.record({"type": "request_start"})
    recorder.record({"type": "agent_end", "agent_id": "child"})
    recorder.record({"type": "request_start"})
    recorder.close()
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert events[2]["agent_id"] == "child" and events[4]["agent_id"] == "main"
    assert len({e["run_id"] for e in events}) == 1
    with pytest.raises(FileExistsError):
        RunRecorder(path)


def test_price_file_requires_exact_model_and_valid_nonnegative_rates(tmp_path):
    path = tmp_path / "prices.json"
    data = asdict(prices())
    path.write_text(json.dumps(data))
    assert Prices.load(path, model="fixture") == prices()
    with pytest.raises(ValueError, match="model"):
        Prices.load(path, model="other")
    data["input_per_million"] = -1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Prices.load(path, model="fixture")


def test_config_snapshot_omits_key_and_url_credentials():
    config = ModelConfig("openai-chat", "fixture", "https://name:secret@host.invalid/v1?token=secret", "top-secret")
    serialized = json.dumps(safe_config(config))
    assert "secret" not in serialized and "api_key" not in serialized
    assert safe_config(config)["base_url"] == "https://host.invalid/v1"
