"""Life runtime primitives.

The life package is the new prompt-engineering-oriented runtime skeleton.
Runtime code carries events, time, patches, tools, and logs; LLM prompts keep
the autonomous judgment.
"""

from .event_pool import InformationPool
from .runtime import LifeRuntime
from .stream_patch import InnerStreamPatch, apply_inner_stream_patch
from .time_awareness import TimeAwareness

__all__ = [
    "InformationPool",
    "InnerStreamPatch",
    "LifeRuntime",
    "TimeAwareness",
    "apply_inner_stream_patch",
]
