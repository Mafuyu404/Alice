"""Windows foreground and running application enumeration."""

from __future__ import annotations

import logging

logger = logging.getLogger("vision")

EXCLUDE_TITLES = {"", "Program Manager", "Settings", "Microsoft Text Input Application"}
EXCLUDE_PROCESSES = {"explorer.exe", "ApplicationFrameHost.exe", "shellexperiencehost.exe",
                     "SearchApp.exe", "TextInputHost.exe", "Widgets.exe", "StartMenuExperienceHost.exe"}
EXCLUDE_CLASSES = {"Shell_TrayWnd", "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow",
                   "Windows.UI.Composition.DesktopWindowContentBridge",
                   "Progman", "WorkerW", "SysListView32", "#32770", "MultitaskingViewFrame",
                   "XamlExplorerHostIslandWindow", "SnipToolWindow"}



def _process_name(pid: int) -> str:
    """Get executable name from PID. Returns 'unknown' on failure."""
    try:
        import win32api
        import win32con
        import win32process

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False, pid,
        )
        if not handle:
            return "unknown"
        try:
            path = win32process.GetModuleFileNameEx(handle, 0)
            return path.rsplit("\\", 1)[-1].lower() if path else "unknown"
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return "unknown"


def get_foreground_app() -> dict | None:
    """Return info about the currently focused window, or *None* if unavailable.

    Result keys: ``title``, ``process``, ``pid``, ``class_name``.
    """
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        return {"title": title, "process": _process_name(pid), "pid": pid, "class_name": cls}
    except Exception as exc:
        logger.debug("get_foreground_app failed: %s", exc)
        return None


def get_running_apps() -> list[dict]:
    """Enumerate all top-level visible windows with non-empty titles.

    Returns a list of dicts with ``title``, ``process``, ``pid``, ``class_name``.

    Shell background windows and known system chrome are filtered out.
    """
    import win32gui
    import win32process

    apps: list[dict] = []
    seen: set[str] = set()

    def _enum_cb(hwnd: int, _) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title or title in EXCLUDE_TITLES:
            return
        cls = win32gui.GetClassName(hwnd)
        if cls in EXCLUDE_CLASSES:
            return

        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        proc = _process_name(pid)
        if proc in EXCLUDE_PROCESSES:
            return

        key = f"{proc}|{title}|{pid}"
        if key in seen:
            return
        seen.add(key)

        apps.append({"title": title, "process": proc, "pid": pid, "class_name": cls})

    win32gui.EnumWindows(_enum_cb, None)
    return apps


def format_apps(apps: list[dict], foreground: dict | None) -> str:
    """Format enumerated apps and foreground window into a readable text block."""
    lines: list[str] = []

    if foreground and foreground.get("title"):
        lines.append(f"[前台焦点] {foreground['title']} ({foreground['process']}, PID={foreground['pid']})")
        lines.append("")

    if apps:
        lines.append("[正在运行的窗口]")
        # sort by process name for grouping
        sorted_apps = sorted(apps, key=lambda a: (a["process"], a["title"]))
        for a in sorted_apps:
            marker = " <- 前台" if (foreground and a["pid"] == foreground["pid"]) else ""
            lines.append(f"  - {a['title']} ({a['process']}, PID={a['pid']}){marker}")
        lines.append("")

    return "\n".join(lines)
