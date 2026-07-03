"""VTube Studio action tool module."""

from kokoro.action.tools.vts.spec import register

__all__ = [
    "VTSBodyDriver",
    "VTSController",
    "VTSExpressionArbiter",
    "VTSIdleLoop",
    "VTSLipSync",
    "VTSRuntime",
    "direct_route",
    "register",
    "start",
]


def __getattr__(name: str):
    if name in {"VTSController", "VTSExpressionArbiter", "VTSIdleLoop", "VTSLipSync"}:
        from kokoro.action.tools.vts import controller

        return getattr(controller, name)
    if name == "VTSBodyDriver":
        from kokoro.action.tools.vts.body_driver import VTSBodyDriver

        return VTSBodyDriver
    if name in {"VTSRuntime", "start"}:
        from kokoro.action.tools.vts import runtime

        return getattr(runtime, name)
    if name == "direct_route":
        from kokoro.action.tools.vts import route

        return route.direct_route
    raise AttributeError(name)
