"""Screen observation action tool module."""

from kokoro.action.tools.observe_screen.spec import register

__all__ = [
    "analyze_screen_interest",
    "analyze_image",
    "capture_edge_cache",
    "edge_cache_config_from_dict",
    "format_edge_cache_for_prompt",
    "get_foreground_app",
    "get_screen_interest_cache",
    "get_cached_screen_interest",
    "handle_screen_command",
    "read_edge_cache",
    "register",
    "write_edge_error_cache",
]


def edge_cache_config_from_dict(config: dict):
    from kokoro.action.tools.observe_screen import edge_cache

    return edge_cache.config_from_dict(config)


def read_edge_cache(path_value: str):
    from kokoro.action.tools.observe_screen import edge_cache

    return edge_cache.read_cache(path_value)


def format_edge_cache_for_prompt(path_value: str, max_chars: int = 4000) -> str:
    from kokoro.action.tools.observe_screen import edge_cache

    return edge_cache.format_for_prompt(path_value, max_chars=max_chars)


def capture_edge_cache(config) -> dict:
    from kokoro.action.tools.observe_screen import edge_cache

    return edge_cache.capture_and_save(config)


def write_edge_error_cache(path_value: str, message: str) -> None:
    from kokoro.action.tools.observe_screen import edge_cache

    edge_cache.write_error_cache(path_value, message)


def get_screen_interest_cache():
    from kokoro.action.tools.observe_screen import screen_interest

    return screen_interest.get_cache()


def get_cached_screen_interest():
    return get_screen_interest_cache().get()


def analyze_screen_interest(*args, **kwargs):
    from kokoro.action.tools.observe_screen import screen_interest

    return screen_interest.analyze(*args, **kwargs)


def get_foreground_app():
    from kokoro.action.tools.observe_screen import vision

    return vision.get_foreground_app()


def analyze_image(*args, **kwargs):
    from kokoro.action.tools.observe_screen import vision

    return vision.analyze_image(*args, **kwargs)


def handle_screen_command(*args, **kwargs):
    from kokoro.action.tools.observe_screen import user_commands

    return user_commands.handle_screen_command(*args, **kwargs)
