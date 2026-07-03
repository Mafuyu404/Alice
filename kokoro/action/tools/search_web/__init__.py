"""Web search action tool module."""

from kokoro.action.tools.search_web.spec import register

__all__ = [
    "WebSearchClient",
    "WebSearchDaemonRuntime",
    "create_client",
    "format_search_result",
    "register",
    "start_daemon",
    "start_runtime",
    "stop_daemon",
]


def __getattr__(name: str):
    if name in {"WebSearchClient", "create_client", "format_search_result"}:
        from kokoro.action.tools.search_web import client

        return getattr(client, name)
    if name == "start_daemon":
        from kokoro.action.tools.search_web.daemon import start

        return start
    if name == "start_runtime":
        from kokoro.action.tools.search_web.daemon import start_runtime

        return start_runtime
    if name == "stop_daemon":
        from kokoro.action.tools.search_web.daemon import stop

        return stop
    if name == "WebSearchDaemonRuntime":
        from kokoro.action.tools.search_web.daemon import WebSearchDaemonRuntime

        return WebSearchDaemonRuntime
    raise AttributeError(name)
