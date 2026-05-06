"""Hierarchical state machine for Alice voice pipeline.

Two-level design:
  Level 1 — SystemState: what the system as a whole is doing
  Level 2 — Component sub-states: STT, TTS, Portrait, Proactive internal state

Thread-safe: all state mutations go through emit() under a single lock.
Observer pattern: subscribe to state changes for side effects.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Optional


# ═══════════════════════════════════════════════════════════════════════════════
# System-level states
# ═══════════════════════════════════════════════════════════════════════════════

class SystemState(StrEnum):
    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    SCREEN_WATCHING = "SCREEN_WATCHING"
    ERROR = "ERROR"
    SHUTTING_DOWN = "SHUTTING_DOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# Component-level states
# ═══════════════════════════════════════════════════════════════════════════════

class STTState(StrEnum):
    INACTIVE = "INACTIVE"
    LISTENING = "LISTENING"
    PAUSED = "PAUSED"


class TTSState(StrEnum):
    IDLE = "IDLE"
    STREAMING = "STREAMING"
    DRAINING = "DRAINING"


class PoolState(StrEnum):
    COLLECTING = "COLLECTING"
    REFINING = "REFINING"
    READY = "READY"


class PortraitState(StrEnum):
    INACTIVE = "INACTIVE"
    SLIDESHOW = "SLIDESHOW"
    DECIDING = "DECIDING"
    NEUTRAL = "NEUTRAL"


class ProactiveState(StrEnum):
    DISABLED = "DISABLED"
    ACCRUING = "ACCRUING"
    DECIDING = "DECIDING"
    DEFERRED = "DEFERRED"
    EXECUTING = "EXECUTING"


# ═══════════════════════════════════════════════════════════════════════════════
# Events
# ═══════════════════════════════════════════════════════════════════════════════

class SystemEvent(StrEnum):
    # Lifecycle
    INIT_DONE = "init_done"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    FATAL = "fatal"

    # STT → Pool pipeline
    USER_SPEECH_START = "user_speech_start"
    USER_SPEECH_END = "user_speech_end"
    STT_REFINED = "stt_refined"

    # Command detection
    COMMAND_DETECTED = "command_detected"
    COMMAND_COMPLETED = "command_completed"

    # LLM
    LLM_START = "llm_start"
    LLM_DONE = "llm_done"

    # TTS
    TTS_START = "tts_start"
    TTS_DONE = "tts_done"

    # Proactive
    PROACTIVE_TRIGGERED = "proactive_triggered"
    PROACTIVE_DEFERRED = "proactive_deferred"
    PROACTIVE_DONE = "proactive_done"

    # Screen watch
    SCREEN_INTEREST = "screen_interest"
    SCREEN_WATCH_DONE = "screen_watch_done"

    # Memory
    MEMORY_EVENT = "memory_event"


# ═══════════════════════════════════════════════════════════════════════════════
# Transition definition
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Transition:
    from_state: SystemState | frozenset[SystemState]
    event: SystemEvent
    to_state: SystemState
    guard: Optional[Callable[[], bool]] = None
    description: str = ""


def _any(*states: SystemState) -> frozenset[SystemState]:
    return frozenset(states)


# ═══════════════════════════════════════════════════════════════════════════════
# State snapshot (serializable, for debug overlay)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class StateSnapshot:
    system: SystemState
    stt: STTState
    tts: TTSState
    pool: PoolState
    portrait: PortraitState
    proactive: ProactiveState
    error_count: int
    last_transition: str
    last_transition_time: float
    uptime: float

    def as_dict(self) -> dict:
        return {
            "system": self.system.value,
            "stt": self.stt.value,
            "tts": self.tts.value,
            "pool": self.pool.value,
            "portrait": self.portrait.value,
            "proactive": self.proactive.value,
            "error_count": self.error_count,
            "last_transition": self.last_transition,
            "uptime": f"{self.uptime:.0f}s",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# State machine
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _TransitionRecord:
    event: SystemEvent
    from_state: SystemState
    to_state: SystemState
    timestamp: float


class SystemStateMachine:
    """Thread-safe hierarchical state machine for the Alice voice pipeline."""

    def __init__(self, max_consecutive_errors: int = 3):
        self._lock = threading.Lock()
        self._created_at = time.monotonic()

        # System state
        self._system_state = SystemState.INITIALIZING

        # Component states
        self._stt_state = STTState.INACTIVE
        self._tts_state = TTSState.IDLE
        self._pool_state = PoolState.COLLECTING
        self._portrait_state = PortraitState.INACTIVE
        self._proactive_state = ProactiveState.DISABLED

        # Internal
        self._observers: list[Callable[[SystemState, SystemState, SystemEvent], None]] = []
        self._transition_log: list[_TransitionRecord] = []
        self._max_log = 64
        self._error_count = 0
        self._max_errors = max_consecutive_errors
        self._error_reset_at = 0.0

        # Transition table
        self._transitions: dict[SystemState, dict[SystemEvent, tuple[SystemState, Optional[Callable[[], bool]]]]] = {}
        self._build_table()

    # ── public properties ──────────────────────────────────────────────────

    @property
    def state(self) -> SystemState:
        with self._lock:
            return self._system_state

    @property
    def stt_state(self) -> STTState:
        with self._lock:
            return self._stt_state

    @property
    def tts_state(self) -> TTSState:
        with self._lock:
            return self._tts_state

    @property
    def pool_state(self) -> PoolState:
        with self._lock:
            return self._pool_state

    @property
    def portrait_state(self) -> PortraitState:
        with self._lock:
            return self._portrait_state

    @property
    def proactive_state(self) -> ProactiveState:
        with self._lock:
            return self._proactive_state

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._system_state in (SystemState.THINKING, SystemState.SPEAKING)

    @property
    def is_idle(self) -> bool:
        with self._lock:
            return self._system_state == SystemState.IDLE

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._system_state == SystemState.LISTENING

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._system_state == SystemState.SPEAKING

    @property
    def is_thinking(self) -> bool:
        with self._lock:
            return self._system_state == SystemState.THINKING

    @property
    def is_shutting_down(self) -> bool:
        with self._lock:
            return self._system_state == SystemState.SHUTTING_DOWN

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    @property
    def can_accept_speech(self) -> bool:
        """Can the system accept new user speech input?"""
        with self._lock:
            return self._system_state in (
                SystemState.IDLE,
                SystemState.LISTENING,
                SystemState.SCREEN_WATCHING,
            )

    @property
    def can_start_conversation(self) -> bool:
        """Can a new conversation turn start (user or proactive)?"""
        with self._lock:
            return self._system_state in (
                SystemState.IDLE,
                SystemState.LISTENING,
                SystemState.SCREEN_WATCHING,
            )

    # ── component state setters (called by components directly) ────────────

    def set_stt_state(self, state: STTState) -> None:
        with self._lock:
            self._stt_state = state

    def set_tts_state(self, state: TTSState) -> None:
        with self._lock:
            self._tts_state = state

    def set_pool_state(self, state: PoolState) -> None:
        with self._lock:
            self._pool_state = state

    def set_portrait_state(self, state: PortraitState) -> None:
        with self._lock:
            self._portrait_state = state

    def set_proactive_state(self, state: ProactiveState) -> None:
        with self._lock:
            self._proactive_state = state

    # ── observers ──────────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[SystemState, SystemState, SystemEvent], None]) -> None:
        """Register a callback: callback(old_state, new_state, event)."""
        with self._lock:
            self._observers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        with self._lock:
            self._observers = [cb for cb in self._observers if cb is not callback]

    # ── main API: emit event ───────────────────────────────────────────────

    def emit(self, event: SystemEvent) -> bool:
        """Fire an event. Returns True if a transition occurred."""
        with self._lock:
            old_state = self._system_state
            transitions = self._transitions.get(old_state, {})
            entry = transitions.get(event)

            if entry is None:
                return False

            to_state, guard = entry
            if guard is not None and not guard():
                return False

            # Execute transition
            self._system_state = to_state
            self._record(old_state, to_state, event)

            # Notify observers outside the lock? No — observers should be fast
            # and we want ordered delivery.
            for observer in self._observers:
                try:
                    observer(old_state, to_state, event)
                except Exception:
                    pass

            return True

    # ── special: error recovery ────────────────────────────────────────────

    def emit_error(self, source: str = "") -> bool:
        """Record an error. Auto-escalates to FATAL after max_consecutive_errors."""
        with self._lock:
            self._error_count += 1
            if self._error_count >= self._max_errors:
                return self.emit(SystemEvent.FATAL)
            # Recoverable: go to ERROR then auto-recover to IDLE
            old = self._system_state
            self._system_state = SystemState.ERROR
            self._record(old, SystemState.ERROR, SystemEvent.ERROR)

            # Schedule auto-recovery (caller should sleep; we can't block here)
            for observer in self._observers:
                try:
                    observer(old, SystemState.ERROR, SystemEvent.ERROR)
                except Exception:
                    pass
            return True

    def recover_from_error(self) -> bool:
        """Transition from ERROR back to IDLE. Call after cleanup."""
        with self._lock:
            if self._system_state != SystemState.ERROR:
                return False
            old = self._system_state
            self._system_state = SystemState.IDLE
            self._record(old, SystemState.IDLE, SystemEvent.INIT_DONE)
            for observer in self._observers:
                try:
                    observer(old, SystemState.IDLE, SystemEvent.INIT_DONE)
                except Exception:
                    pass
            return True

    def reset_error_count(self) -> None:
        with self._lock:
            if time.monotonic() - self._error_reset_at > 60.0:
                self._error_count = 0
                self._error_reset_at = time.monotonic()

    # ── snapshot ───────────────────────────────────────────────────────────

    def snapshot(self) -> StateSnapshot:
        with self._lock:
            last = self._transition_log[-1] if self._transition_log else None
            return StateSnapshot(
                system=self._system_state,
                stt=self._stt_state,
                tts=self._tts_state,
                pool=self._pool_state,
                portrait=self._portrait_state,
                proactive=self._proactive_state,
                error_count=self._error_count,
                last_transition=f"{last.from_state.value}→{last.to_state.value} ({last.event.value})" if last else "",
                last_transition_time=last.timestamp if last else 0.0,
                uptime=time.monotonic() - self._created_at,
            )

    # ── internal ───────────────────────────────────────────────────────────

    def _record(self, from_state: SystemState, to_state: SystemState, event: SystemEvent) -> None:
        self._transition_log.append(_TransitionRecord(
            event=event,
            from_state=from_state,
            to_state=to_state,
            timestamp=time.monotonic(),
        ))
        if len(self._transition_log) > self._max_log:
            self._transition_log[:] = self._transition_log[-self._max_log:]

    def _build_table(self) -> None:
        """Define all valid state transitions with optional guards."""
        # IDLE transitions
        self._transitions[SystemState.IDLE] = {
            SystemEvent.USER_SPEECH_START: (SystemState.LISTENING, None),
            SystemEvent.PROACTIVE_TRIGGERED: (SystemState.THINKING, None),
            SystemEvent.SCREEN_INTEREST: (SystemState.SCREEN_WATCHING, None),
            SystemEvent.SHUTDOWN: (SystemState.SHUTTING_DOWN, None),
            SystemEvent.ERROR: (SystemState.ERROR, None),
            SystemEvent.FATAL: (SystemState.SHUTTING_DOWN, None),
        }

        # LISTENING transitions
        self._transitions[SystemState.LISTENING] = {
            SystemEvent.USER_SPEECH_END: (SystemState.IDLE, None),
            SystemEvent.STT_REFINED: (SystemState.THINKING, None),
            SystemEvent.COMMAND_DETECTED: (SystemState.THINKING, None),
            SystemEvent.SHUTDOWN: (SystemState.SHUTTING_DOWN, None),
            SystemEvent.ERROR: (SystemState.ERROR, None),
            SystemEvent.FATAL: (SystemState.SHUTTING_DOWN, None),
        }

        # THINKING transitions
        self._transitions[SystemState.THINKING] = {
            SystemEvent.LLM_DONE: (SystemState.SPEAKING, None),
            SystemEvent.SHUTDOWN: (SystemState.SHUTTING_DOWN, None),
            SystemEvent.ERROR: (SystemState.ERROR, None),
            SystemEvent.FATAL: (SystemState.SHUTTING_DOWN, None),
        }

        # SPEAKING transitions
        self._transitions[SystemState.SPEAKING] = {
            SystemEvent.TTS_DONE: (SystemState.IDLE, None),
            SystemEvent.USER_SPEECH_START: (SystemState.LISTENING, None),
            SystemEvent.SHUTDOWN: (SystemState.SHUTTING_DOWN, None),
            SystemEvent.ERROR: (SystemState.ERROR, None),
            SystemEvent.FATAL: (SystemState.SHUTTING_DOWN, None),
        }

        # PROACTIVE_SPEECH merged into THINKING path — same LLM→TTS pipeline

        # SCREEN_WATCHING transitions
        self._transitions[SystemState.SCREEN_WATCHING] = {
            SystemEvent.SCREEN_WATCH_DONE: (SystemState.IDLE, None),
            SystemEvent.USER_SPEECH_START: (SystemState.LISTENING, None),
            SystemEvent.SHUTDOWN: (SystemState.SHUTTING_DOWN, None),
            SystemEvent.ERROR: (SystemState.ERROR, None),
            SystemEvent.FATAL: (SystemState.SHUTTING_DOWN, None),
        }

        # ERROR transitions
        self._transitions[SystemState.ERROR] = {
            SystemEvent.INIT_DONE: (SystemState.IDLE, None),  # recover
            SystemEvent.FATAL: (SystemState.SHUTTING_DOWN, None),
            SystemEvent.SHUTDOWN: (SystemState.SHUTTING_DOWN, None),
        }

        # INITIALIZING transitions
        self._transitions[SystemState.INITIALIZING] = {
            SystemEvent.INIT_DONE: (SystemState.IDLE, None),
            SystemEvent.FATAL: (SystemState.SHUTTING_DOWN, None),
            SystemEvent.SHUTDOWN: (SystemState.SHUTTING_DOWN, None),
        }

        # SHUTTING_DOWN — terminal, no transitions out
        self._transitions[SystemState.SHUTTING_DOWN] = {}
