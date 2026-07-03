"""Motion script models and script factories for VTS body driver."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from kokoro.action.tools.vts.body_utils import _clamp, _float


class MotionScript:
    channel: str
    mood: str = ""
    energy: float = 0.35
    duration: float = 3.0
    motions: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    created_at: float = field(default_factory=time.monotonic)

    @property
    def end_at(self) -> float:
        return self.created_at + max(0.5, self.duration)


def _script_from_data(data: Any, *, channel: str) -> MotionScript | None:
    if not isinstance(data, dict):
        return None
    motions = data.get("motions")
    if not isinstance(motions, list):
        motions = []
    safe_motions = [m for m in motions if isinstance(m, dict)][:8]
    if not safe_motions:
        return None
    return MotionScript(
        channel=channel,
        mood=str(data.get("mood") or data.get("base_mood") or "").strip()[:80],
        energy=_clamp(_float(data.get("energy"), 0.35), 0.0, 1.0),
        duration=_clamp(_float(data.get("duration"), 3.0), 0.8, 8.0),
        motions=safe_motions,
        reason=str(data.get("reason") or "").strip()[:200],
    )


def _sanitize_idle_body_script(script: MotionScript | None) -> MotionScript:
    if script is None:
        return _fallback_body_script("左右待机")
    motions: list[dict[str, Any]] = []
    for motion in script.motions:
        target = str(motion.get("target") or "").lower()
        kind = str(motion.get("kind") or "").lower()
        safe = dict(motion)
        if target == "body" and kind in {"breath", "bounce"}:
            safe["kind"] = "bounce"
            safe["amplitude"] = min(0.62, max(0.22, _float(safe.get("amplitude"), 0.32)))
            safe["frequency"] = min(0.58, max(0.20, _float(safe.get("frequency"), 0.30)))
            safe.setdefault("phase", random.random())
            motions.append(safe)
            continue
        if target == "head" and kind in {"sway", "shake"}:
            axis = str(safe.get("axis") or "z").lower()
            if axis == "y":
                axis = random.choice(["x", "z"])
            safe["axis"] = axis
            if axis == "x":
                safe["amplitude"] = max(5.0, min(9.0, _float(safe.get("amplitude"), 6.2)))
                safe["frequency"] = max(0.16, min(0.36, _float(safe.get("frequency"), 0.24)))
            else:
                safe["amplitude"] = max(6.5, min(10.0, _float(safe.get("amplitude"), 7.4)))
                safe["frequency"] = max(0.22, min(0.48, _float(safe.get("frequency"), 0.32)))
            safe.setdefault("phase", random.random())
            motions.append(safe)
            continue
        if target == "head" and kind == "nod":
            # Nods read as front/back bobbing on this model; avoid them while idle.
            continue
    axes = {str(m.get("axis") or "").lower() for m in motions if str(m.get("target") or "").lower() == "head"}
    if "z" not in axes:
        motions.append({"target": "head", "kind": "sway", "axis": "z", "amplitude": random.uniform(7.0, 9.6), "frequency": random.uniform(0.24, 0.40), "phase": random.random()})
    if "x" not in axes and random.random() < 0.85:
        motions.append({"target": "head", "kind": "sway", "axis": "x", "amplitude": random.uniform(5.0, 8.2), "frequency": random.uniform(0.14, 0.30), "phase": random.random()})
    if not any(str(m.get("target")).lower() == "body" for m in motions):
        motions.append({"target": "body", "kind": "bounce", "amplitude": random.uniform(0.26, 0.48), "frequency": random.uniform(0.22, 0.36), "phase": random.random()})
    return MotionScript(
        channel="body",
        mood="左右待机",
        energy=max(0.68, min(0.95, script.energy)),
        duration=max(7.0, min(10.0, script.duration + 1.5)),
        motions=motions[:5],
        reason=script.reason or "待机时用身体轻轻左右摇摆",
    )


def _idle_face_life_script() -> MotionScript:
    return MotionScript(
        channel="face",
        mood="idle_smile",
        energy=0.45,
        duration=random.uniform(6.5, 9.5),
        motions=[
            {"target": "mouth", "kind": "smile", "value": random.uniform(0.18, 0.34)},
            {"target": "eyes", "kind": "blink", "interval": random.uniform(2.8, 4.8), "phase": random.random()},
            {"target": "eyes", "kind": "look", "x": random.uniform(-0.14, 0.14), "y": random.uniform(-0.04, 0.08)},
        ],
        reason="idle smile and natural glance",
    )


def _fallback_face_script(mood: str, energy: float = 0.35) -> MotionScript:
    return MotionScript(
        channel="face",
        mood=mood,
        energy=min(0.45, max(0.3, energy)),
        duration=random.uniform(6.5, 9.5),
        motions=[
            {"target": "mouth", "kind": "smile", "value": random.uniform(0.16, 0.30)},
            {"target": "eyes", "kind": "blink", "interval": random.uniform(2.8, 5.0), "phase": random.random()},
            {"target": "eyes", "kind": "look", "x": random.uniform(-0.12, 0.12), "y": random.uniform(-0.04, 0.07)},
        ],
        reason="本地稳定待机脸部动作",
    )


def _fallback_body_script(mood: str, energy: float = 0.35) -> MotionScript:
    energy = max(0.65, energy)
    style = random.choice(["sway", "look_left", "look_right", "bouncy"])
    if style == "look_left":
        x_phase = 0.12
    elif style == "look_right":
        x_phase = 0.62
    else:
        x_phase = random.random()
    return MotionScript(
        channel="body",
        mood=mood,
        energy=energy,
        duration=random.uniform(7.0, 10.0),
        motions=[
            {"target": "body", "kind": "bounce", "amplitude": random.uniform(0.30, 0.54), "frequency": random.uniform(0.22, 0.36), "phase": random.random()},
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": random.uniform(7.0, 10.0), "frequency": random.uniform(0.24, 0.40), "phase": random.random()},
            {"target": "head", "kind": "sway", "axis": "x", "amplitude": random.uniform(5.0, 8.5), "frequency": random.uniform(0.14, 0.30), "phase": x_phase},
        ],
        reason="本地左右摇摆待机身体动作",
    )


def _direct_motion_scripts(motion: str, *, intensity: float, duration: float, reason: str) -> tuple[MotionScript, MotionScript]:
    key = (motion or "idle").lower()
    energy = max(0.25, intensity)
    amp = 3.5 + energy * 6.0
    face_motions: list[dict[str, Any]] = [
        {"target": "eyes", "kind": "blink", "interval": 2.2, "phase": random.random()},
    ]
    body_motions: list[dict[str, Any]] = [
        {"target": "body", "kind": "bounce", "amplitude": 0.45 + energy * 0.28, "frequency": 0.32},
    ]

    if key in {"smile", "happy"}:
        face_motions += [
            {"target": "mouth", "kind": "smile", "value": 0.35 + energy * 0.35},
            {"target": "eyes", "kind": "squint", "value": 0.12 + energy * 0.18},
        ]
        body_motions += [
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": amp * 0.75, "frequency": 0.65},
            {"target": "body", "kind": "bounce", "amplitude": 0.55 + energy * 0.35, "frequency": 0.75},
        ]
    elif key in {"shake", "sway", "excited"}:
        face_motions += [
            {"target": "mouth", "kind": "smile", "value": 0.25 + energy * 0.35},
            {"target": "eyes", "kind": "look", "x": random.choice([-0.12, 0.12]), "y": 0.02},
        ]
        body_motions += [
            {"target": "head", "kind": "shake", "axis": "x", "amplitude": amp, "frequency": 0.85 + energy * 0.45},
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": amp * 0.65, "frequency": 0.55},
            {"target": "body", "kind": "bounce", "amplitude": 0.55 + energy * 0.35, "frequency": 0.9},
        ]
    elif key == "nod":
        face_motions += [{"target": "mouth", "kind": "smile", "value": 0.18 + energy * 0.2}]
        body_motions += [
            {"target": "head", "kind": "nod", "amplitude": amp, "frequency": 0.75 + energy * 0.35},
            {"target": "body", "kind": "bounce", "amplitude": 0.45 + energy * 0.28, "frequency": 0.7},
        ]
    elif key in {"pout", "sad"}:
        face_motions += [
            {"target": "mouth", "kind": "pout", "value": 0.25 + energy * 0.25},
            {"target": "eyes", "kind": "sleepy", "value": 0.18 + energy * 0.22},
        ]
        body_motions += [
            {"target": "head", "kind": "droop", "amplitude": 1.2 + energy * 2.0},
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": 0.8 + energy, "frequency": 0.24},
        ]
    elif key == "thinking":
        face_motions += [
            {"target": "eyes", "kind": "look", "x": 0.18, "y": 0.06},
            {"target": "brows", "kind": "frown", "value": 0.18 + energy * 0.15},
        ]
        body_motions += [{"target": "head", "kind": "tilt", "value": -2.0 - energy * 2.0, "amplitude": 1.0}]
    else:
        face_motions += [{"target": "mouth", "kind": "smile", "value": 0.12 + energy * 0.18}]
        body_motions += [
            {"target": "head", "kind": "sway", "axis": "x", "amplitude": amp * 0.45, "frequency": 0.35},
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": amp * 0.25, "frequency": 0.28, "phase": 0.25},
        ]

    return (
        MotionScript("face", mood=key, energy=energy, duration=duration, motions=face_motions, reason=reason),
        MotionScript("body", mood=key, energy=energy, duration=duration, motions=body_motions, reason=reason),
    )
