"""Proactive speech scheduler.

The scheduler implements the first usable slice of alice.md: desire accrual,
disturbance filtering, cooldowns, and diversity for idle/recent proactive
speech. Screen and memory hooks are represented so callers can add events
without changing the scheduling core later.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import StrEnum

from kokoro import prompts


class Behavior(StrEnum):
    IDLE = "IDLE"
    RECENT = "RECENT"
    MEM = "MEM"
    SCREEN = "SCREEN"


@dataclass(frozen=True)
class ProactiveDecision:
    behavior: Behavior
    desire: float
    disturbance: float
    prompt: str


@dataclass
class IdleConfig:
    enabled: bool = True
    rate: float = 0.02
    active_threshold: float = 70.0
    cooldown_seconds: float = 30.0
    defer_penalty: float = 20.0
    max_disturbance: float = 35.0
    weight: float = 1.0
    user_idle_bonus_after_seconds: float = 120.0


@dataclass
class RecentConfig:
    enabled: bool = True
    decay_rate: float = 2.0
    decay_delay_seconds: float = 30.0
    active_threshold: float = 70.0
    cooldown_seconds: float = 30.0
    defer_penalty: float = 20.0
    max_disturbance: float = 45.0
    weight: float = 1.0
    bonus_window_seconds: float = 120.0


@dataclass
class MemConfig:
    enabled: bool = True
    rate: float = 0.01
    active_threshold: float = 70.0
    cooldown_seconds: float = 30.0
    defer_penalty: float = 20.0
    max_disturbance: float = 25.0
    weight: float = 1.0


@dataclass
class ScreenConfig:
    enabled: bool = True
    decay_rate: float = 5.0
    active_threshold: float = 70.0
    cooldown_seconds: float = 30.0
    defer_penalty: float = 20.0
    max_disturbance: float = 50.0
    weight: float = 1.0
    post_conversation_decay: float = 50.0


@dataclass
class ProactiveConfig:
    enabled: bool = False
    tick_seconds: float = 20.0
    drive_rate: float = 1.0
    diversity_window_seconds: float = 120.0
    secondary_pick_chance: float = 0.2
    idle: IdleConfig = field(default_factory=IdleConfig)
    recent: RecentConfig = field(default_factory=RecentConfig)
    mem: MemConfig = field(default_factory=MemConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)

    @property
    def behavior_configs(self) -> dict[Behavior, IdleConfig | RecentConfig | MemConfig | ScreenConfig]:
        return {
            Behavior.IDLE: self.idle,
            Behavior.RECENT: self.recent,
            Behavior.MEM: self.mem,
            Behavior.SCREEN: self.screen,
        }


@dataclass
class ProactiveScheduler:
    config: ProactiveConfig
    desires: dict[Behavior, float] = field(default_factory=dict)
    cooldown_until: dict[Behavior, float] = field(default_factory=dict)
    history: list[tuple[Behavior, float]] = field(default_factory=list)
    screen_context: str = ""
    memory_context: str = ""
    last_tick: float = field(default_factory=time.monotonic)
    last_user_activity: float = field(default_factory=time.monotonic)
    last_conversation_end: float | None = None
    quiet_until: float = 0.0
    recent_blocked_until: float = 0.0
    _deferred: ProactiveDecision | None = None

    def __post_init__(self) -> None:
        for behavior in Behavior:
            self.desires.setdefault(behavior, 0.0)
            self.cooldown_until.setdefault(behavior, 0.0)

    def reset_all(self) -> None:
        for behavior in Behavior:
            self.desires[behavior] = 0.0
        self.last_tick = time.monotonic()
        self.recent_blocked_until = 0.0
        self._deferred = None

    def record_user_activity(self) -> None:
        self.last_user_activity = time.monotonic()

    def record_conversation_end(self, user_text: str, assistant_text: str) -> None:
        now = time.monotonic()
        self.last_conversation_end = now
        self.last_user_activity = now
        self.desires[Behavior.RECENT] = max(
            self.desires[Behavior.RECENT],
            self._conversation_quality(user_text, assistant_text),
        )
        # Reduce SCREEN desire after conversation to avoid overlap
        self.desires[Behavior.SCREEN] = max(
            0.0,
            self.desires[Behavior.SCREEN] - self.config.screen.post_conversation_decay,
        )

    def record_tts_end(self) -> None:
        """Block RECENT for one tick after TTS finishes, so the character
        doesn't immediately follow up while the user might be processing."""
        self.recent_blocked_until = time.monotonic() + self.config.tick_seconds

    def add_screen_interest(self, score: float, context: str = "") -> None:
        if score > 50:
            self.desires[Behavior.SCREEN] = max(self.desires[Behavior.SCREEN], min(score, 100.0))
            self.screen_context = context.strip()[:600]

    def add_memory_interest(self, score: float = 40.0, context: str = "") -> None:
        self.desires[Behavior.MEM] = min(100.0, self.desires[Behavior.MEM] + max(0.0, score))
        if context:
            self.memory_context = context.strip()[:800]

    def apply_feedback(self, behavior: Behavior, positive: bool) -> None:
        factor = 1.05 if positive else 0.8
        bc = self.config.behavior_configs[behavior]
        bc.weight = _clamp(bc.weight * factor, 0.2, 2.0)
        if not positive:
            self.quiet_until = time.monotonic() + 600.0

    def tick(self, disturbance: float | None = None, busy: bool = False) -> ProactiveDecision | None:
        if not self.config.enabled:
            return None

        now = time.monotonic()
        dt = max(0.0, now - self.last_tick)
        self.last_tick = now
        current_disturbance = self.calculate_disturbance(disturbance)

        # Handle a previously-deferred decision when no longer busy
        if self._deferred is not None:
            if busy:
                return None  # still busy, keep waiting
            decision = self._deferred
            self._deferred = None
            self._update_desires(dt, now)
            bc = self.config.behavior_configs[decision.behavior]
            reduced = max(0.0, decision.desire - bc.defer_penalty)
            if reduced >= bc.active_threshold:
                self.desires[decision.behavior] = 0.0
                self.cooldown_until[decision.behavior] = now + bc.cooldown_seconds
                self.history.append((decision.behavior, now))
                self.history = [(b, ts) for b, ts in self.history if now - ts <= 600.0]
                return ProactiveDecision(
                    behavior=decision.behavior,
                    desire=reduced,
                    disturbance=current_disturbance,
                    prompt=self.prompt_for(decision.behavior),
                )
            return None  # penalty dropped below threshold

        # Normal flow
        candidates = self._candidates(now, current_disturbance)
        self._update_desires(dt, now)
        selected = self._select_with_diversity(candidates, now)
        if selected is None:
            return None

        behavior, desire = selected
        bc = self.config.behavior_configs[behavior]

        # If TTS/STT is active, defer the decision
        if busy:
            self._deferred = ProactiveDecision(
                behavior=behavior,
                desire=desire,
                disturbance=current_disturbance,
                prompt=self.prompt_for(behavior),
            )
            return None

        # Proceed now
        self.desires[behavior] = 0.0
        self.cooldown_until[behavior] = now + bc.cooldown_seconds
        self.history.append((behavior, now))
        self.history = [(b, ts) for b, ts in self.history if now - ts <= 600.0]
        return ProactiveDecision(
            behavior=behavior,
            desire=desire,
            disturbance=current_disturbance,
            prompt=self.prompt_for(behavior),
        )

    def calculate_disturbance(self, explicit: float | None = None) -> float:
        if time.monotonic() < self.quiet_until:
            return 100.0
        if explicit is not None:
            return _clamp(explicit, 0.0, 100.0)

        idle_for = time.monotonic() - self.last_user_activity
        if idle_for < 10.0:
            return 30.0
        if idle_for < 60.0:
            return 20.0
        return 10.0

    def prompt_for(self, behavior: Behavior) -> str:
        key = {
            Behavior.IDLE: "proactive.idle",
            Behavior.RECENT: "proactive.recent",
            Behavior.MEM: "proactive.mem",
            Behavior.SCREEN: "proactive.screen",
        }[behavior]
        prompt = prompts.get(key, "")
        if behavior == Behavior.SCREEN and self.screen_context:
            prompt += prompts.format_prompt("proactive.screen_context_label", context=self.screen_context)
        if behavior == Behavior.MEM and self.memory_context:
            prompt += prompts.format_prompt("proactive.mem_context_label", context=self.memory_context)
        return prompt

    def snapshot(self) -> dict[str, object]:
        disturbance = self.calculate_disturbance()
        return {
            "enabled": self.config.enabled,
            "desires": {behavior.value: round(value, 2) for behavior, value in self.desires.items()},
            "disturbance": round(disturbance, 2),
            "thresholds": {
                behavior.value: bc.active_threshold
                for behavior, bc in self.config.behavior_configs.items()
            },
            "candidates": [
                behavior.value
                for behavior, desire in self.desires.items()
                if desire >= self.config.behavior_configs[behavior].active_threshold
                and disturbance <= self.config.behavior_configs[behavior].max_disturbance
            ],
        }

    def _update_desires(self, dt: float, now: float) -> None:
        dr = self.config.drive_rate
        idle = self.config.idle
        if idle.enabled:
            idle_gain = idle.rate * idle.weight * dt * dr
            if now - self.last_user_activity > idle.user_idle_bonus_after_seconds:
                idle_gain += idle.rate * idle.weight * 3.0 * dt * dr
            if self.last_conversation_end and now - self.last_conversation_end < self.config.recent.bonus_window_seconds:
                idle_gain += idle.rate * idle.weight * 2.0 * dt * dr
            self.desires[Behavior.IDLE] = min(100.0, self.desires[Behavior.IDLE] + idle_gain)
        else:
            self.desires[Behavior.IDLE] = 0.0

        mem = self.config.mem
        if mem.enabled:
            self.desires[Behavior.MEM] = min(
                100.0,
                self.desires[Behavior.MEM] + mem.rate * mem.weight * dt * dr,
            )
        else:
            self.desires[Behavior.MEM] = 0.0

        recent = self.config.recent
        if recent.enabled:
            if self.last_conversation_end is None or now - self.last_conversation_end >= recent.decay_delay_seconds:
                self.desires[Behavior.RECENT] = max(0.0, self.desires[Behavior.RECENT] - recent.decay_rate * dt)
        else:
            self.desires[Behavior.RECENT] = 0.0

        screen = self.config.screen
        if screen.enabled:
            self.desires[Behavior.SCREEN] = max(0.0, self.desires[Behavior.SCREEN] - screen.decay_rate * dt)
        else:
            self.desires[Behavior.SCREEN] = 0.0

    def _candidates(self, now: float, disturbance: float) -> list[tuple[Behavior, float]]:
        candidates: list[tuple[Behavior, float]] = []
        for behavior, desire in self.desires.items():
            if now < self.cooldown_until[behavior]:
                continue
            bc = self.config.behavior_configs[behavior]
            if not bc.enabled:
                continue
            if desire < bc.active_threshold:
                continue
            if behavior == Behavior.RECENT and now < self.recent_blocked_until:
                continue
            if disturbance <= bc.max_disturbance:
                candidates.append((behavior, desire))
        return candidates

    def _select_with_diversity(
        self,
        candidates: list[tuple[Behavior, float]],
        now: float,
    ) -> tuple[Behavior, float] | None:
        recent_behaviors = {
            behavior
            for behavior, ts in self.history
            if now - ts <= self.config.diversity_window_seconds
        }
        filtered = [candidate for candidate in candidates if candidate[0] not in recent_behaviors]
        if not filtered:
            return None

        ordered = sorted(filtered, key=lambda item: item[1], reverse=True)
        if len(ordered) > 1 and random.random() < self.config.secondary_pick_chance:
            return ordered[1]
        return ordered[0]

    @staticmethod
    def _conversation_quality(user_text: str, assistant_text: str) -> float:
        combined_len = len(user_text.strip()) + len(assistant_text.strip())
        if combined_len <= 0:
            return 0.0
        length_score = min(70.0, combined_len / 4.0)
        warmth = 0.0
        positive_tokens = ("haha", "lol", "thanks", "thank", "good", "great", "哈哈", "谢谢", "可以", "继续")
        text = f"{user_text} {assistant_text}".lower()
        if any(token in text for token in positive_tokens):
            warmth = 15.0
        return _clamp(30.0 + length_score + warmth, 0.0, 100.0)


def from_config(config: dict) -> ProactiveScheduler:
    section = config.get("proactive", {})
    if not isinstance(section, dict):
        section = {}

    def number(key: str, default: float) -> float:
        try:
            return float(section.get(key, default))
        except (TypeError, ValueError):
            return default

    def sub_number(sub: dict, key: str, default: float) -> float:
        try:
            return float(sub.get(key, default))
        except (TypeError, ValueError):
            return default

    def sub_bool(sub: dict, key: str, default: bool) -> bool:
        val = sub.get(key, default)
        return bool(val) if isinstance(val, bool) else default

    idle_section = section.get("idle", {})
    if not isinstance(idle_section, dict):
        idle_section = {}
    recent_section = section.get("recent", {})
    if not isinstance(recent_section, dict):
        recent_section = {}
    mem_section = section.get("mem", {})
    if not isinstance(mem_section, dict):
        mem_section = {}
    screen_section = section.get("screen", {})
    if not isinstance(screen_section, dict):
        screen_section = {}

    cfg = ProactiveConfig(
        enabled=bool(section.get("enabled", config.get("proactive_enabled", False))),
        tick_seconds=max(1.0, number("tick_seconds", 20.0)),
        drive_rate=max(0.0, number("drive_rate", 1.0)),
        diversity_window_seconds=max(0.0, number("diversity_window_seconds", 120.0)),
        secondary_pick_chance=number("secondary_pick_chance", 0.2),
        idle=IdleConfig(
            enabled=sub_bool(idle_section, "enabled", True),
            rate=max(0.0, sub_number(idle_section, "rate", 0.02)),
            active_threshold=_clamp(sub_number(idle_section, "active_threshold", 70.0), 60.0, 85.0),
            cooldown_seconds=max(0.0, sub_number(idle_section, "cooldown_seconds", 30.0)),
            defer_penalty=max(0.0, sub_number(idle_section, "defer_penalty", 20.0)),
            max_disturbance=sub_number(idle_section, "max_disturbance", 35.0),
            weight=sub_number(idle_section, "weight", 1.0),
            user_idle_bonus_after_seconds=max(0.0, sub_number(idle_section, "user_idle_bonus_after_seconds", 120.0)),
        ),
        recent=RecentConfig(
            enabled=sub_bool(recent_section, "enabled", True),
            decay_rate=max(0.0, sub_number(recent_section, "decay_rate", 2.0)),
            decay_delay_seconds=max(0.0, sub_number(recent_section, "decay_delay_seconds", 30.0)),
            active_threshold=_clamp(sub_number(recent_section, "active_threshold", 70.0), 60.0, 85.0),
            cooldown_seconds=max(0.0, sub_number(recent_section, "cooldown_seconds", 30.0)),
            defer_penalty=max(0.0, sub_number(recent_section, "defer_penalty", 20.0)),
            max_disturbance=sub_number(recent_section, "max_disturbance", 45.0),
            weight=sub_number(recent_section, "weight", 1.0),
            bonus_window_seconds=max(0.0, sub_number(recent_section, "bonus_window_seconds", 120.0)),
        ),
        mem=MemConfig(
            enabled=sub_bool(mem_section, "enabled", True),
            rate=max(0.0, sub_number(mem_section, "rate", 0.01)),
            active_threshold=_clamp(sub_number(mem_section, "active_threshold", 70.0), 60.0, 85.0),
            cooldown_seconds=max(0.0, sub_number(mem_section, "cooldown_seconds", 30.0)),
            defer_penalty=max(0.0, sub_number(mem_section, "defer_penalty", 20.0)),
            max_disturbance=sub_number(mem_section, "max_disturbance", 25.0),
            weight=sub_number(mem_section, "weight", 1.0),
        ),
        screen=ScreenConfig(
            enabled=sub_bool(screen_section, "enabled", True),
            decay_rate=max(0.0, sub_number(screen_section, "decay_rate", 5.0)),
            active_threshold=_clamp(sub_number(screen_section, "active_threshold", 70.0), 60.0, 85.0),
            cooldown_seconds=max(0.0, sub_number(screen_section, "cooldown_seconds", 30.0)),
            defer_penalty=max(0.0, sub_number(screen_section, "defer_penalty", 20.0)),
            max_disturbance=sub_number(screen_section, "max_disturbance", 50.0),
            weight=sub_number(screen_section, "weight", 1.0),
            post_conversation_decay=max(0.0, sub_number(screen_section, "post_conversation_decay", 50.0)),
        ),
    )
    return ProactiveScheduler(cfg)


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
