"""Send judgment requests to Claude under a hard cap, through one of two backends.

`api` (the Anthropic API, capped in dollars): requests go through the Message Batches API when it is enabled, in waves: a
wave is as many requests as the remaining budget covers at their worst-case
cost (the input at the cache-write rate plus `max_tokens` of output). A batch
still running after the timeout is cancelled, and its unfinished requests are
sent as direct calls, which are also the fallback when a batch cannot be
created. Direct calls reserve their worst-case cost before they are sent, so
the cap is never passed; each reservation is replaced by the cost of the
usage the API reports.

`claude-code` (Claude Code run headless on a Claude subscription, capped in
calls): each request is one `claude -p` process with the request's system
prompt, the user content on stdin, the model and effort, and structured output
against the request's schema. It has no tools, no settings, MCP servers or
slash commands, and runs in an empty temporary directory, so the model answers
only from the content it is given. A reply with no usable JSON is asked once
more. Each process counts as one call against the cap; a usage limit stops the
run's calls, and the requests not sent go back to the pool.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
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
# Turns one `claude -p` call may take: the answer through the structured-output
# tool, and its retries when the answer does not match the schema.
CLAUDE_MAX_TURNS = 4


class BudgetError(RuntimeError):
    pass


class ClaudeCodeError(RuntimeError):
    """Claude Code cannot run at all (not installed, not logged in): the run stops."""


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
    """Dollars (`unit="usd"`) or calls (`unit="calls"`) spent and reserved against a
    cap, saved to an optional ledger file so separate commands in one run share the
    cap. With calls, the dollars Claude Code reports are kept as `usd_equivalent`."""

    def __init__(self, cap: float, ledger: Path | None = None, *, unit: str = "usd"):
        self.cap = float(cap)
        self.ledger = ledger
        self.unit = unit
        self.spent = 0.0
        self.reserved = 0.0
        self.requests = 0
        self.usd_equivalent = 0.0
        self.stopped = False  # a request was held back for lack of budget
        self.exhausted = False  # nothing more may be sent (a usage limit was reached)
        self._lock = threading.Lock()
        if ledger and ledger.exists():
            data = json.loads(ledger.read_text(encoding="utf-8"))
            if data.get("unit", "usd") == unit:
                key = "calls" if unit == "calls" else "spent_usd"
                self.spent = float(data.get(key, 0.0))
                self.requests = int(data.get("requests", 0))
                self.usd_equivalent = float(data.get("usd_equivalent", 0.0))

    @property
    def remaining(self) -> float:
        return self.cap - self.spent - self.reserved

    def reserve(self, amount: float) -> bool:
        with self._lock:
            if self.exhausted or self.spent + self.reserved + amount > self.cap:
                self.stopped = True
                return False
            self.reserved += amount
            return True

    def settle(self, reserved: float, actual: float, *, usd: float = 0.0) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)
            self.spent += actual
            self.requests += 1
            if self.unit == "calls":
                self.usd_equivalent += usd
            self._save()

    def release(self, reserved: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)

    def exhaust(self) -> None:
        with self._lock:
            self.exhausted = self.stopped = True

    def _save(self) -> None:
        if self.ledger is None:
            return
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        if self.unit == "calls":
            data: dict[str, Any] = {
                "unit": "calls",
                "cap_calls": int(self.cap),
                "calls": int(self.spent),
                "requests": self.requests,
                "usd_equivalent": round(self.usd_equivalent, 6),
            }
        else:
            data = {
                "cap_usd": self.cap,
                "spent_usd": round(self.spent, 6),
                "requests": self.requests,
            }
        self.ledger.write_text(json.dumps(data) + "\n", encoding="utf-8")

    def as_dict(self) -> dict[str, Any]:
        if self.unit == "calls":
            return {
                "backend": "claude-code",
                "unit": "calls",
                "cap_calls": int(self.cap),
                "calls": int(self.spent),
                "requests": self.requests,
                "usd_equivalent": round(self.usd_equivalent, 4),
                "stopped_by_cap": self.stopped,
            }
        return {
            "cap_usd": self.cap,
            "spent_usd": round(self.spent, 4),
            "requests": self.requests,
            "stopped_by_cap": self.stopped,
        }


class CappedBudget:
    """A share of a Budget with a cap of its own: a request is sent only when it fits
    both, and what it costs is counted in both."""

    def __init__(self, parent: Budget, cap: float):
        self.parent = parent
        self.unit = parent.unit
        self.cap = float(cap)
        self.spent = 0.0
        self.reserved = 0.0
        self.requests = 0
        self.usd_equivalent = 0.0
        self.stopped = False
        self._lock = threading.Lock()

    @property
    def remaining(self) -> float:
        return min(self.cap - self.spent - self.reserved, self.parent.remaining)

    def reserve(self, amount: float) -> bool:
        with self._lock:
            if self.spent + self.reserved + amount > self.cap or not self.parent.reserve(amount):
                self.stopped = True
                return False
            self.reserved += amount
            return True

    def settle(self, reserved: float, actual: float, *, usd: float = 0.0) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)
            self.spent += actual
            self.requests += 1
            self.usd_equivalent += usd
        self.parent.settle(reserved, actual, usd=usd)

    def release(self, reserved: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)
        self.parent.release(reserved)

    def exhaust(self) -> None:
        self.stopped = True
        self.parent.exhaust()

    def as_dict(self) -> dict[str, Any]:
        if self.unit == "calls":
            return {
                "backend": "claude-code",
                "unit": "calls",
                "cap_calls": int(self.cap),
                "calls": int(self.spent),
                "requests": self.requests,
                "usd_equivalent": round(self.usd_equivalent, 4),
                "stopped_by_cap": self.stopped,
                "run_cap_calls": int(self.parent.cap),
                "run_calls": int(self.parent.spent),
            }
        return {
            "cap_usd": self.cap,
            "spent_usd": round(self.spent, 4),
            "requests": self.requests,
            "stopped_by_cap": self.stopped,
            "run_cap_usd": self.parent.cap,
            "run_spent_usd": round(self.parent.spent, 4),
        }


def make_budget(settings: Settings, ledger: Path | None = None) -> Budget:
    """The run's budget for the settings' backend: calls for claude-code, dollars for api."""
    if settings.backend == "claude-code":
        return Budget(settings.call_cap, ledger, unit="calls")
    return Budget(settings.spend_cap_usd, ledger)


def spend_line(spend: Mapping[str, Any]) -> str:
    """'Spent $x of $y' or 'Used n of m Claude Code calls', from a budget's `as_dict`."""
    if spend.get("unit") == "calls":
        return (
            f"Used {spend.get('calls', 0)} of {spend.get('cap_calls', 0)} Claude Code calls"
            f" (${spend.get('usd_equivalent', 0):.2f} at API prices)"
        )
    return f"Spent ${spend.get('spent_usd', 0):.2f} of ${spend.get('cap_usd', 0):.2f}"


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
    via: str | None = None  # "batch", "direct" or "claude-code"; None when never sent

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


# -- Claude Code ------------------------------------------------------------------------

# Variables of a surrounding Claude Code session (when the command runs inside one),
# which make a nested `claude -p` act as that session's child; and API credentials,
# which Claude Code would use instead of the subscription.
_DROP_ENV = (
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)
_KEEP_ENV = ("CLAUDE_CODE_OAUTH_TOKEN",)
_AUTH = re.compile(
    r"not logged in|/login|invalid api key|oauth token|authentication|unauthorized", re.I
)
_LIMIT = re.compile(r"usage limit|rate limit|hit your limit|limit reached|limit will reset", re.I)


def claude_env(base: Mapping[str, str], max_tokens: int) -> dict[str, str]:
    """The environment of one `claude -p` process: the subscription token (from
    CLAUDE_CODE_OAUTH_TOKEN or the login Claude Code has stored), never an API key."""
    env = {
        k: v
        for k, v in base.items()
        if k not in _DROP_ENV and (not k.startswith("CLAUDE_CODE_") or k in _KEEP_ENV)
    }
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_tokens)
    env["DISABLE_AUTOUPDATER"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def claude_args(
    executable: str, model: str, effort: str, system_file: str, schema: Any
) -> list[str]:
    return [
        executable,
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--effort",
        effort,
        "--system-prompt-file",
        system_file,
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-turns",
        str(CLAUDE_MAX_TURNS),
    ]


@dataclass
class ClaudeResult:
    data: Any = None
    error: str | None = None
    retry: bool = False  # the reply had no usable JSON; worth asking once more
    limit: bool = False  # a usage or rate limit was reached
    usage: dict[str, int] = field(default_factory=dict)
    usd: float = 0.0


def _short(text: Any, n: int = 300) -> str:
    return " ".join(str(text or "").split())[:n]


def _json_text(text: str) -> Any:
    """JSON from a reply's text, with a surrounding Markdown code fence allowed."""
    t = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```$", t, re.S)
    return json.loads(fence.group(1) if fence else t)


def parse_claude(stdout: str, returncode: int, stderr: str, schema: Any) -> ClaudeResult:
    """The outcome of one `claude -p --output-format json` process."""
    out = None
    for line in reversed(stdout.strip().splitlines()):
        try:
            out = json.loads(line)
            break
        except ValueError:
            continue
    if not isinstance(out, dict):
        why = _short(stderr) or _short(stdout) or f"exit status {returncode}"
        if _AUTH.search(why):
            raise ClaudeCodeError(f"Claude Code cannot authenticate: {why}")
        return ClaudeResult(
            error=f"Claude Code gave no result ({why})", limit=bool(_LIMIT.search(why))
        )
    usage = usage_dict(out.get("usage") or {})
    usd = float(out.get("total_cost_usd") or 0.0)
    text = out.get("result")
    if out.get("is_error") or (out.get("subtype") or "success") != "success":
        why = _short(text) or str(out.get("subtype"))
        status = out.get("api_error_status")
        if status in (401, 403) or (status is None and _AUTH.search(why)):
            raise ClaudeCodeError(f"Claude Code cannot authenticate: {why}")
        if status == 429 or _LIMIT.search(why):
            return ClaudeResult(
                error=f"Claude usage limit: {why}", limit=True, usage=usage, usd=usd
            )
        retry = str(out.get("subtype", "")).startswith("error_max")
        return ClaudeResult(error=f"Claude Code error: {why}", retry=retry, usage=usage, usd=usd)
    if out.get("stop_reason") == "refusal":
        return ClaudeResult(error="the model declined the request", usage=usage, usd=usd)
    data = out.get("structured_output")
    if data is None:
        try:
            data = _json_text(text or "")
        except ValueError as exc:
            return ClaudeResult(
                error=f"the reply is not JSON ({exc})", retry=True, usage=usage, usd=usd
            )
        import jsonschema

        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            return ClaudeResult(
                error=f"the reply does not match the schema ({_short(exc.message, 200)})",
                retry=True,
                usage=usage,
                usd=usd,
            )
    return ClaudeResult(data=data, usage=usage, usd=usd)


class ClaudeCode:
    """Runs one request as a `claude -p` process."""

    def __init__(
        self,
        settings: Settings,
        runner: Callable[..., Any] | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        self.settings = settings
        self.runner = runner or subprocess.run
        self.environ = environ

    def invoke(self, req: Request) -> ClaudeResult:
        s = self.settings
        with tempfile.TemporaryDirectory(prefix="edictai-claude-") as tmp:
            system = Path(tmp) / "system.md"
            system.write_text(req.system, encoding="utf-8")
            work = Path(tmp) / "work"
            work.mkdir()
            args = claude_args(s.claude_executable, s.model, s.effort, str(system), req.schema)
            env = claude_env(os.environ if self.environ is None else self.environ, req.max_tokens)
            try:
                proc = self.runner(
                    args,
                    input=req.user,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=str(work),
                    env=env,
                    timeout=s.claude_timeout_seconds,
                    check=False,
                )
            except FileNotFoundError:
                raise ClaudeCodeError(
                    f"Claude Code is not installed ({s.claude_executable!r} not found)"
                ) from None
        return parse_claude(proc.stdout or "", proc.returncode, proc.stderr or "", req.schema)


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
        runner: Callable[..., Any] | None = None,
    ):
        self.settings = settings
        self.backend = settings.backend
        # Claude Code calls are capped by count; their dollars come from Claude Code.
        self.price = settings.price() if self.backend == "api" else None
        self.budget = budget
        self._client = client
        self.claude = ClaudeCode(settings, runner) if self.backend == "claude-code" else None
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
        if self.price is None:
            return 1.0  # one call
        tokens = estimate_tokens(req.system) + estimate_tokens(req.user)
        rate = max(self.price.input, self.price.cache_write)
        return (tokens * rate + req.max_tokens * self.price.output) / 1_000_000 * discount

    def run(self, requests: Iterable[Request]) -> dict[str, Reply]:
        reqs = list(requests)
        replies: dict[str, Reply] = {}
        pending = reqs
        if self.backend == "api" and self.settings.use_batches and reqs:
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

    def call(self, req: Request, *, stream: bool = False) -> Reply:
        """One direct call under the cap. `stream` sends it as a streamed request, for
        replies whose `max_tokens` is too large for a plain one to finish in time."""
        if self.claude is not None:
            return self._claude_call(req)
        import anthropic

        amount = self.worst_case(req)
        if not self.budget.reserve(amount):
            return Reply(req.id, error="not sent: spending cap reached")
        try:
            if stream:
                with self.client.messages.stream(**self.params(req)) as s:
                    message = s.get_final_message()
            else:
                message = self.client.messages.create(**self.params(req))
        except anthropic.APIError as exc:
            self.budget.release(amount)
            return Reply(req.id, error=f"API error: {type(exc).__name__}: {exc}")
        usage = usage_dict(message.usage)
        cost = cost_of(usage, self.price)
        self.budget.settle(amount, cost)
        data, error = parse_message(message)
        return Reply(req.id, data, error, usage, cost, "direct")

    def _claude_call(self, req: Request) -> Reply:
        """One request through Claude Code, asked once more when the reply has no
        usable JSON. Each process is one call against the cap."""
        assert self.claude is not None
        result = ClaudeResult()
        for attempt in (1, 2):
            if not self.budget.reserve(1.0):
                return Reply(req.id, error="not sent: spending cap reached (Claude Code calls)")
            try:
                result = self.claude.invoke(req)
            except subprocess.TimeoutExpired:
                self.budget.settle(1.0, 1.0)
                limit = self.settings.claude_timeout_seconds
                return Reply(
                    req.id, error=f"Claude Code timed out after {limit:.0f}s", via="claude-code"
                )
            except ClaudeCodeError:
                self.budget.release(1.0)
                raise
            self.budget.settle(1.0, 1.0, usd=result.usd)
            if result.limit:
                self.budget.exhaust()
                self.log(f"{req.id}: {result.error}; no more Claude Code calls this run")
                return Reply(req.id, error=f"not sent: spending cap reached ({result.error})")
            if result.retry and attempt == 1:
                self.log(f"{req.id}: {result.error}; asking once more")
                continue
            break
        return Reply(req.id, result.data, result.error, result.usage, result.usd, "claude-code")

    def _direct(self, reqs: list[Request], replies: dict[str, Reply]) -> None:
        one = self.call
        workers = max(
            1,
            self.settings.claude_workers
            if self.backend == "claude-code"
            else self.settings.direct_workers,
        )
        if workers == 1 or len(reqs) == 1:
            results = [one(r) for r in reqs]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one, reqs))
        for reply in results:
            replies[reply.id] = reply
        if reqs:
            how = "Claude Code calls" if self.backend == "claude-code" else "direct calls"
            self.log(f"{len(reqs)} {how}")
