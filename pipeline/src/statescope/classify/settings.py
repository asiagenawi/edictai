"""Settings for the automated classification run, from pipeline/automation.yaml."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import Paths
from ..yamlio import load_yaml

DEFAULT_MODEL = "claude-sonnet-5-5"
SPEND_CAP_ENV = "WEEKLY_SPEND_CAP_USD"
BACKEND_ENV = "EDICTAI_LLM_BACKEND"
OAUTH_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
BACKENDS = ("api", "claude-code")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Price:
    """USD per million tokens at standard rates."""

    input: float
    output: float
    cache_write: float
    cache_read: float

    @classmethod
    def from_mapping(cls, model: str, data: Mapping[str, Any]) -> Price:
        try:
            base = float(data["input"])
            out = float(data["output"])
        except (KeyError, TypeError, ValueError):
            raise SettingsError(f"prices.{model} needs numeric input and output") from None
        return cls(
            input=base,
            output=out,
            cache_write=float(data.get("cache_write", base * 1.25)),
            cache_read=float(data.get("cache_read", base)),
        )


@dataclass(frozen=True)
class Settings:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    items_per_request: Mapping[str, int] = field(default_factory=lambda: {"triage": 25, "full": 1})
    max_tokens: Mapping[str, int] = field(default_factory=lambda: {"triage": 12000, "full": 8000})
    use_batches: bool = True
    batch_poll_seconds: float = 30
    batch_timeout_minutes: float = 90
    direct_workers: int = 4
    text_version: str = "ai"
    spend_cap_usd: float = 10.0
    legiscan_text_queries: int = 500
    legiscan_monthly_reserve: int = 500
    federal_text_requests: int = 300
    precision_floor: float = 0.85
    recall_floor: float = 0.9
    prices: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    batch_discount: float = 0.5
    # "api" (the Anthropic API, budgeted in dollars) or "claude-code" (Claude Code
    # run headless on a subscription, budgeted in calls).
    backend: str = "api"
    call_cap: int = 400
    claude_executable: str = "claude"
    claude_timeout_seconds: float = 1800
    claude_workers: int = 2

    def price(self, model: str | None = None) -> Price:
        model = model or self.model
        data = self.prices.get(model)
        if data is None:
            raise SettingsError(
                f"no price for {model} in pipeline/automation.yaml; the run cannot be budgeted"
            )
        return Price.from_mapping(model, data)

    def with_overrides(self, **values: Any) -> Settings:
        clean = {k: v for k, v in values.items() if v is not None}
        return Settings(**{**self.__dict__, **clean})


def resolve_backend(env: Mapping[str, str]) -> str:
    """EDICTAI_LLM_BACKEND: claude-code, api, or auto (the default): claude-code when
    CLAUDE_CODE_OAUTH_TOKEN is set, otherwise api."""
    value = (env.get(BACKEND_ENV) or "auto").strip().lower()
    if value == "auto":
        return "claude-code" if (env.get(OAUTH_ENV) or "").strip() else "api"
    if value not in BACKENDS:
        raise SettingsError(f"{BACKEND_ENV} must be claude-code, api, or auto, not {value!r}")
    return value


def settings_path(paths: Paths) -> Path:
    return paths.root / "pipeline" / "automation.yaml"


def load_settings(paths: Paths, env: Mapping[str, str] | None = None) -> Settings:
    """Settings from pipeline/automation.yaml (defaults when it is absent), with the
    spend cap taken from WEEKLY_SPEND_CAP_USD when that is set and the backend from
    EDICTAI_LLM_BACKEND (`resolve_backend`)."""
    env = os.environ if env is None else env
    path = settings_path(paths)
    data = (load_yaml(path) or {}) if path.exists() else {}
    c = data.get("classify") or {}
    b = data.get("budget") or {}
    e = data.get("eval") or {}
    cc = data.get("claude_code") or {}
    base = Settings()
    values: dict[str, Any] = {
        "model": c.get("model", base.model),
        "effort": c.get("effort", base.effort),
        "items_per_request": {**base.items_per_request, **(c.get("items_per_request") or {})},
        "max_tokens": {**base.max_tokens, **(c.get("max_tokens") or {})},
        "use_batches": bool(c.get("use_batches", base.use_batches)),
        "batch_poll_seconds": float(c.get("batch_poll_seconds", base.batch_poll_seconds)),
        "batch_timeout_minutes": float(c.get("batch_timeout_minutes", base.batch_timeout_minutes)),
        "direct_workers": int(c.get("direct_workers", base.direct_workers)),
        "text_version": c.get("text_version", base.text_version),
        "spend_cap_usd": float(b.get("spend_cap_usd", base.spend_cap_usd)),
        "legiscan_text_queries": int(b.get("legiscan_text_queries", base.legiscan_text_queries)),
        "legiscan_monthly_reserve": int(
            b.get("legiscan_monthly_reserve", base.legiscan_monthly_reserve)
        ),
        "federal_text_requests": int(b.get("federal_text_requests", base.federal_text_requests)),
        "precision_floor": float(e.get("precision_floor", base.precision_floor)),
        "recall_floor": float(e.get("recall_floor", base.recall_floor)),
        "prices": data.get("prices") or {},
        "batch_discount": float(data.get("batch_discount", base.batch_discount)),
        "backend": resolve_backend(env),
        "call_cap": int(b.get("claude_code_calls", base.call_cap)),
        "claude_executable": str(cc.get("executable", base.claude_executable)),
        "claude_timeout_seconds": float(cc.get("timeout_seconds", base.claude_timeout_seconds)),
        "claude_workers": int(cc.get("workers", base.claude_workers)),
    }
    cap = (env.get(SPEND_CAP_ENV) or "").strip()
    if cap:
        try:
            values["spend_cap_usd"] = float(cap)
        except ValueError:
            raise SettingsError(f"{SPEND_CAP_ENV} is not a number: {cap!r}") from None
    return Settings(**values)
