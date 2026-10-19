"""Send judgment requests to the Anthropic API under a hard spending cap.

Requests go through the Message Batches API when it is enabled, in waves: a
wave is as many requests as the remaining budget covers at their worst-case
cost (the input at the cache-write rate plus `max_tokens` of output). A batch
still running after the timeout is cancelled, and its unfinished requests are
sent as direct calls, which are also the fallback when a batch cannot be
created. Direct calls reserve their worst-case cost before they are sent, so
the cap is never passed; each reservation is replaced by the cost of the
usage the API reports.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .settings import Price, Settings

# Characters per token, rounded down so the input estimate is an upper bound.
CHARS_PER_TOKEN = 3
# Schema keywords the structured-output grammar does not take. `check` still
# enforces every one of them on the returned judgments.
_UNSUPPORTED = {
    "$schema",
    "title",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minimum",
    "maximum",
    "pattern",
    "if",
    "then",
    "else",
}
BATCH_CANCEL_WAIT = 600.0  # seconds to wait for a cancelled batch to end


class BudgetError(RuntimeError):
    pass


# -- schemas --------------------------------------------------------------------


def _strip(node: Any) -> Any:
    if isinstance(node, dict):
        out = {k: _strip(v) for k, v in node.items() if k not in _UNSUPPORTED}
        if "enum" in out and "type" not in out and all(isinstance(v, str) for v in out["enum"]):
            out["type"] = "string"
        return out
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def api_schema(line_schema: Mapping[str, Any]) -> dict[str, Any]:
    """The structured-output schema for one reply: `{"judgments": [line, ...]}`, where
    `line` is the judgment-file schema without the keywords the grammar does not take."""
    return {
        "type": "object",
        "properties": {"judgments": {"type": "array", "items": _strip(dict(line_schema))}},
        "required": ["judgments"],
        "additionalProperties": False,
    }


# -- cost -------------------------------------------------------------------------


def usage_dict(usage: Any) -> dict[str, int]:
    def n(name: str) -> int:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, Mapping):
            value = usage.get(name)
        return int(value or 0)

    return {
        "input_tokens": n("input_tokens"),
        "output_tokens": n("output_tokens"),
        "cache_creation_input_tokens": n("cache_creation_input_tokens"),
        "cache_read_input_tokens": n("cache_read_input_tokens"),
    }


def cost_of(usage: Mapping[str, int], price: Price, discount: float = 1.0) -> float:
    dollars = (
        usage.get("input_tokens", 0) * price.input
        + usage.get("output_tokens", 0) * price.output
        + usage.get("cache_creation_input_tokens", 0) * price.cache_write
        + usage.get("cache_read_input_tokens", 0) * price.cache_read
    ) / 1_000_000
    return dollars * discount


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) + 16


class Budget:
    """Dollars spent and reserved against a cap, saved to an optional ledger file so
    separate commands in one run share the cap."""

    def __init__(self, cap: float, ledger: Path | None = None):
        self.cap = float(cap)
        self.ledger = ledger
        self.spent = 0.0
        self.reserved = 0.0
        self.requests = 0
        self.stopped = False  # a request was held back for lack of budget
        self._lock = threading.Lock()
        if ledger and ledger.exists():
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.spent = float(data.get("spent_usd", 0.0))
            self.requests = int(data.get("requests", 0))

    @property
    def remaining(self) -> float:
        return self.cap - self.spent - self.reserved

    def reserve(self, amount: float) -> bool:
        with self._lock:
            if self.spent + self.reserved + amount > self.cap:
                self.stopped = True
                return False
            self.reserved += amount
            return True

    def settle(self, reserved: float, actual: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)
            self.spent += actual
            self.requests += 1
            self._save()

    def release(self, reserved: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)

    def _save(self) -> None:
        if self.ledger is None:
            return
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(
            json.dumps(
                {"cap_usd": self.cap, "spent_usd": round(self.spent, 6), "requests": self.requests}
            )
            + "\n",
            encoding="utf-8",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "cap_usd": self.cap,
            "spent_usd": round(self.spent, 4),
            "requests": self.requests,
            "stopped_by_cap": self.stopped,
        }


# -- requests -----------------------------------------------------------------------


@dataclass
class Request:
    id: str  # [A-Za-z0-9_-]{1,64}
    system: str
    user: str
    schema: dict[str, Any]
    max_tokens: int


@dataclass
class Reply:
    id: str
    data: Any = None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost: float = 0.0
    via: str | None = None  # "batch" or "direct"; None when never sent

    @property
    def sent(self) -> bool:
        return self.via is not None


def parse_message(message: Any) -> tuple[Any, str | None]:
    """(parsed JSON, None) or (None, why the reply is unusable)."""
    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        return None, "the model declined the request"
    if stop == "max_tokens":
        return None, "the reply was cut off at max_tokens"
    text = next(
        (b.text for b in getattr(message, "content", []) or [] if getattr(b, "type", "") == "text"),
        None,
    )
    if not text:
        return None, "the reply has no text"
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, f"the reply is not JSON ({exc})"


class Judge:
    def __init__(
        self,
        settings: Settings,
        budget: Budget,
        client: Any = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self.price = settings.price()
        self.budget = budget
        self._client = client
        self.sleep = sleep
        self.clock = clock
        self.log = log or (lambda _m: None)
        self.batches: list[str] = []

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def params(self, req: Request) -> dict[str, Any]:
        return {
            "model": self.settings.model,
            "max_tokens": req.max_tokens,
            "system": [
                {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": req.user}],
            "output_config": {
                "effort": self.settings.effort,
                "format": {"type": "json_schema", "schema": req.schema},
            },
        }

    def worst_case(self, req: Request, discount: float = 1.0) -> float:
        tokens = estimate_tokens(req.system) + estimate_tokens(req.user)
        rate = max(self.price.input, self.price.cache_write)
        return (tokens * rate + req.max_tokens * self.price.output) / 1_000_000 * discount

    def run(self, requests: Iterable[Request]) -> dict[str, Reply]:
        reqs = list(requests)
        replies: dict[str, Reply] = {}
        pending = reqs
        if self.settings.use_batches and reqs:
            pending = self._batches(reqs, replies)
        if pending:
            self._direct(pending, replies)
        for req in reqs:
            replies.setdefault(req.id, Reply(req.id, error="not sent: spending cap reached"))
        return replies

    # -- batches ---------------------------------------------------------------

    def _batches(self, reqs: list[Request], replies: dict[str, Reply]) -> list[Request]:
        """Send waves of batches; returns the requests to send as direct calls."""
        import anthropic

        discount = self.settings.batch_discount
        queue = list(reqs)
        direct: list[Request] = []
        while queue:
            wave: list[Request] = []
            held: dict[str, float] = {}
            for req in queue:
                amount = self.worst_case(req, discount)
                if not self.budget.reserve(amount):
                    break
                wave.append(req)
                held[req.id] = amount
            if not wave:
                self.log(f"spending cap reached: {len(queue)} requests not sent")
                return direct
            queue = queue[len(wave) :]
            try:
                batch = self.client.messages.batches.create(
                    requests=[{"custom_id": r.id, "params": self.params(r)} for r in wave]
                )
            except anthropic.APIError as exc:
                for amount in held.values():
                    self.budget.release(amount)
                self.log(f"batch not created ({type(exc).__name__}); sending direct calls")
                return [*direct, *wave, *queue]
            self.batches.append(batch.id)
            self.log(f"batch {batch.id}: {len(wave)} requests")
            ended = self._wait(batch.id)
            by_id = {r.id: r for r in wave}
            if ended:
                for result in self.client.messages.batches.results(batch.id):
                    req = by_id.pop(result.custom_id, None)
                    if req is None:
                        continue
                    amount = held.pop(req.id)
                    kind = result.result.type
                    if kind == "succeeded":
                        message = result.result.message
                        usage = usage_dict(message.usage)
                        cost = cost_of(usage, self.price, discount)
                        self.budget.settle(amount, cost)
                        data, error = parse_message(message)
                        replies[req.id] = Reply(req.id, data, error, usage, cost, "batch")
                        continue
                    self.budget.release(amount)
                    error = getattr(getattr(result.result, "error", None), "error", None)
                    if kind == "errored" and getattr(error, "type", "") == "invalid_request_error":
                        replies[req.id] = Reply(
                            req.id, error=f"invalid request: {getattr(error, 'message', '')}"
                        )
                    else:
                        direct.append(req)
                # Requests the results did not list are sent again directly.
                for req in by_id.values():
                    self.budget.release(held.pop(req.id))
                    direct.append(req)
            else:
                # The batch did not end even after cancelling: its requests may still be
                # processed and billed, so their reservations stay counted as spent.
                for req in wave:
                    amount = held.pop(req.id)
                    self.budget.settle(amount, amount)
                    replies[req.id] = Reply(req.id, error="batch did not end after cancelling")
        return direct

    def _wait(self, batch_id: str) -> bool:
        """Poll until the batch ends; cancel it at the timeout. True when it ended."""
        start = self.clock()
        timeout = self.settings.batch_timeout_minutes * 60
        cancelled_at: float | None = None
        while True:
            batch = self.client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                return True
            now = self.clock()
            if cancelled_at is None and now - start >= timeout:
                self.log(f"batch {batch_id}: timed out; cancelling")
                self.client.messages.batches.cancel(batch_id)
                cancelled_at = now
            elif cancelled_at is not None and now - cancelled_at >= BATCH_CANCEL_WAIT:
                return False
            self.sleep(self.settings.batch_poll_seconds)

    # -- direct calls ----------------------------------------------------------

    def _direct(self, reqs: list[Request], replies: dict[str, Reply]) -> None:
        def one(req: Request) -> Reply:
            import anthropic

            amount = self.worst_case(req)
            if not self.budget.reserve(amount):
                return Reply(req.id, error="not sent: spending cap reached")
            try:
                message = self.client.messages.create(**self.params(req))
            except anthropic.APIError as exc:
                self.budget.release(amount)
                return Reply(req.id, error=f"API error: {type(exc).__name__}: {exc}")
            usage = usage_dict(message.usage)
            cost = cost_of(usage, self.price)
            self.budget.settle(amount, cost)
            data, error = parse_message(message)
            return Reply(req.id, data, error, usage, cost, "direct")

        workers = max(1, self.settings.direct_workers)
        if workers == 1 or len(reqs) == 1:
            results = [one(r) for r in reqs]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one, reqs))
        for reply in results:
            replies[reply.id] = reply
        if reqs:
            self.log(f"{len(reqs)} direct calls")
