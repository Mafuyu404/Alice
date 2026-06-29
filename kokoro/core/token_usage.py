"""Token usage tracking aggregated by model and function.

Usage
-----
    from kokoro.core import token_usage

    # Record a call
    token_usage.record("qwen2.5:7b", "chat", prompt_tokens=100, completion_tokens=50)

    # Record a vision call
    token_usage.record("qwen-vl-plus", "vision_describe", prompt_tokens=200, completion_tokens=30)

    # Print summary
    print(token_usage.summary())
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable


class _TokenTracker:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], dict] = defaultdict(
            lambda: {"calls": 0, "prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0}
        )

    def record(
        self,
        model: str,
        function: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
    ) -> None:
        key = (model, function)
        self._data[key]["calls"] += 1
        self._data[key]["prompt"] += prompt_tokens
        self._data[key]["completion"] += completion_tokens
        self._data[key]["cache_hit"] += cache_hit_tokens
        self._data[key]["cache_miss"] += cache_miss_tokens

    def record_usage(self, model: str, function: str, usage: dict) -> None:
        pt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        ct = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        hit = usage.get("prompt_cache_hit_tokens") or 0
        miss = usage.get("prompt_cache_miss_tokens") or 0
        details = usage.get("prompt_tokens_details")
        if not hit and isinstance(details, dict):
            hit = details.get("cached_tokens") or 0
        if not miss and pt and hit:
            miss = max(0, int(pt) - int(hit))
        if pt or ct or hit or miss:
            self.record(
                model,
                function,
                int(pt),
                int(ct),
                cache_hit_tokens=int(hit),
                cache_miss_tokens=int(miss),
            )

    def make_callback(self, model: str, function: str) -> Callable[[dict], None]:
        """Return a callback that accepts a usage dict and records tokens."""
        def _cb(usage: dict) -> None:
            self.record_usage(model, function, usage)
        return _cb

    def summary(self) -> str:
        if not self._data:
            return "  [token] No model calls recorded."

        lines = ["  [token] Token usage by model × function:"]
        header = (
            f"  {'Model':<22} {'Function':<22} {'Calls':>7} "
            f"{'Input':>14} {'Output':>12} {'Cache Hit':>12} {'Hit%':>7} {'Total':>12}"
        )
        lines.append(header)
        lines.append("  " + "-" * 99)

        total_calls = 0
        total_prompt = 0
        total_completion = 0
        for (model, func), counts in sorted(self._data.items()):
            total_calls += counts["calls"]
            total_prompt += counts["prompt"]
            total_completion += counts["completion"]
            hit = counts.get("cache_hit", 0)
            miss = counts.get("cache_miss", 0)
            denom = hit + miss
            hit_rate = (hit / denom * 100.0) if denom else 0.0
            t = counts["prompt"] + counts["completion"]
            lines.append(
                f"  {model:<22} {func:<22} {counts['calls']:>7} "
                f"{counts['prompt']:>14,} {counts['completion']:>12,} "
                f"{hit:>12,} {hit_rate:>6.1f}% {t:>12,}"
            )

        lines.append("  " + "-" * 99)
        gt = total_prompt + total_completion
        lines.append(
            f"  {'Total':<46} {total_calls:>7} "
            f"{total_prompt:>14,} {total_completion:>12,} "
            f"{'':>12} {'':>7} {gt:>12,}"
        )
        return "\n".join(lines)


_TRACKER = _TokenTracker()


def record(
    model: str,
    function: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> None:
    """Record token usage for a single LLM call."""
    _TRACKER.record(
        model,
        function,
        prompt_tokens,
        completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
    )


def record_usage(model: str, function: str, usage: dict) -> None:
    """Record a provider usage payload, including prompt cache fields."""
    _TRACKER.record_usage(model, function, usage)


def make_callback(model: str, function: str) -> Callable[[dict], None]:
    """Return a callback that records usage when called with a usage dict.

    Intended for use with ``stream_chat(usage_callback=...)``.
    """
    return _TRACKER.make_callback(model, function)


def summary() -> str:
    """Return a formatted summary table of all recorded usage."""
    return _TRACKER.summary()
