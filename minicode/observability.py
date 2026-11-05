"""运行元数据、请求用量与显式价表；不把提示词或工具输出写入计量日志。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


@dataclass(frozen=True)
class Prices:
    model: str
    currency: str
    as_of: str
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float
    cache_write_per_million: float

    @classmethod
    def load(cls, path: Path, *, model: str):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("价表字段必须包含 model/currency/as_of 和四项 *_per_million 单价")
        if data["model"] != model:
            raise ValueError("价表 model 必须与本次请求模型一致")
        if not isinstance(data["currency"], str) or not data["currency"].strip():
            raise ValueError("价表 currency 必须是非空字符串")
        date.fromisoformat(data["as_of"])
        for name in (key for key in data if key.endswith("_per_million")):
            value = data[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"价表 {name} 必须是非负有限数值")
        return cls(**data)


def safe_config(config):
    """只记录允许的字段，永不序列化 api_key。"""
    allowed = ("api", "model", "provider", "base_url", "timeout", "max_tokens", "chat_token_field",
               "thinking", "thinking_budget_tokens", "reasoning_effort", "temperature", "top_p", "tool_choice", "strict_tools",
               "response_format", "response_schema", "max_retries", "retry_base_delay", "retry_max_delay")
    result = {name: getattr(config, name) for name in allowed if hasattr(config, name)}
    if result.get("base_url"):
        parts = urlsplit(result["base_url"])
        result["base_url"] = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
    return result


def usage_parts(raw: dict, normalized: dict):
    """缓存 token 与普通输入互斥；reasoning 是输出的子集，不能再次收费。"""
    total = normalized.get("input_tokens")
    output = normalized.get("output_tokens")
    if total is None or output is None:
        return None
    if "prompt_tokens" in raw:
        hit = raw.get("prompt_cache_hit_tokens", (raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        write = 0
    elif "cache_read_input_tokens" in raw or "cache_creation_input_tokens" in raw:
        hit = raw.get("cache_read_input_tokens", 0)
        write = raw.get("cache_creation_input_tokens", 0)
    else:
        hit = (raw.get("input_tokens_details") or {}).get("cached_tokens", 0)
        write = 0
    numbers = (total, output, hit, write)
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0 for n in numbers):
        return None
    if hit + write > total:
        return None
    return {"input_tokens": total - hit - write, "cache_read_tokens": hit,
            "cache_write_tokens": write, "output_tokens": output}


class RunRecorder:
    def __init__(self, path: Path | None = None, *, prices: Prices | None = None, sink=None):
        self.run_id = uuid4().hex
        self.prices, self.sink = prices, sink
        self.lock = RLock()
        self.handle = None
        self.requests = self.retries = self.unknown_usage = 0
        self.input_tokens = self.output_tokens = 0
        self.known_cost = 0.0
        self.query_id = 0
        self._completed_requests = set()
        self._agents = []
        if path is not None:
            path = path.expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = path.open("x", encoding="utf-8")

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def record(self, event):
        with self.lock:
            event = dict(event)
            event.setdefault("run_id", self.run_id)
            event.setdefault("query_id", self.query_id)
            event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            if event.get("type") == "agent_start":
                self._agents.append(event.get("agent_id", "main"))
            event.setdefault("agent_id", self._agents[-1] if self._agents else "main")
            if event.get("type") == "agent_end" and self._agents:
                if self._agents[-1] == event["agent_id"]:
                    self._agents.pop()
            if event.get("type") == "request_retry":
                self.retries += 1
            if event.get("type") == "request_end":
                identity = event.get("local_request_id")
                if identity is not None:
                    if identity in self._completed_requests:
                        return
                    self._completed_requests.add(identity)
                self.requests += 1
                normalized, raw = event.get("normalized_usage"), event.get("usage")
                if event.get("unbilled") and not raw:
                    # 一次被拒绝后重试的 429 不应让整次运行的费用变成未知。
                    normalized = {"input_tokens": 0, "output_tokens": 0}
                    parts = {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0}
                else:
                    parts = usage_parts(raw, normalized) if isinstance(raw, dict) and isinstance(normalized, dict) else None
                if parts is None:
                    self.unknown_usage += 1
                    event["estimated_cost"] = None
                else:
                    self.input_tokens += normalized["input_tokens"]
                    self.output_tokens += normalized["output_tokens"]
                    if self.prices:
                        p = self.prices
                        cost = (parts["input_tokens"] * p.input_per_million
                                + parts["cache_read_tokens"] * p.cache_read_per_million
                                + parts["cache_write_tokens"] * p.cache_write_per_million
                                + parts["output_tokens"] * p.output_per_million) / 1_000_000
                        event["estimated_cost"] = cost
                        event["currency"] = p.currency
                        self.known_cost += cost
            if self.handle is not None:
                self.handle.write(json.dumps(event, ensure_ascii=False, default=_json_default) + "\n")
                self.handle.flush()
            if self.sink:
                self.sink(event)

    def summary(self):
        with self.lock:
            return {"run_id": self.run_id, "requests": self.requests, "retries": self.retries,
                    "unknown_usage_requests": self.unknown_usage,
                    "observed_usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens},
                    "estimated_cost": self.known_cost if self.prices and not self.unknown_usage else None,
                    "known_cost_subtotal": self.known_cost if self.prices else None,
                    "prices": asdict(self.prices) if self.prices else None}


def _json_default(value):
    return asdict(value) if is_dataclass(value) else str(value)
