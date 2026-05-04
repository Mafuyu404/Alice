from __future__ import annotations

import argparse
import ctypes
import json
import random
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

from kokoro import config as cfg


SLIDE_INTERVAL_MS = 2000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17352
MAP_FILE = "portrait_map.json"
STATE_FILE = "portrait_overlay_state.json"
MIN_SCALE = 0.2
MAX_SCALE = 4.0
SCALE_STEP = 0.1

GWL_EXSTYLE = -20
GWL_STYLE = -16
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_POPUP = 0x80000000

DWMWA_NCRENDERING_POLICY = 2
DWMNCRP_DISABLED = 1
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1
DWMWA_BORDER_COLOR = 34

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi


def load_config(base_dir: Path) -> dict:
    return cfg.load()


class PortraitCatalog:
    def __init__(self, image_dir: Path) -> None:
        self.image_dir = image_dir
        self.map_path = image_dir / MAP_FILE
        self.assets = self._load_assets()
        self.by_name = {asset["new_name"]: asset for asset in self.assets}

    def _load_assets(self) -> list[dict]:
        data = json.loads(self.map_path.read_text(encoding="utf-8"))
        assets = data.get("assets", [])
        for asset in assets:
            asset["path"] = str((self.image_dir / asset["new_name"]).resolve())
        return assets

    def list_series(self) -> list[str]:
        return sorted({asset["series"] for asset in self.assets})

    def filter_assets(
        self,
        series: str | None = None,
        emotion: str | None = None,
        pose: str | None = None,
        eyes: str | None = None,
        mouth: str | None = None,
    ) -> list[dict]:
        results = self.assets
        for field, value in (
            ("series", series),
            ("emotion", emotion),
            ("pose", pose),
            ("eyes", eyes),
            ("mouth", mouth),
        ):
            if value:
                results = [asset for asset in results if asset.get(field) == value]
        return results

    def get_by_name(self, name: str) -> dict | None:
        return self.by_name.get(name)

    def random_asset(self, **filters: str | None) -> dict | None:
        matches = self.filter_assets(**filters)
        return random.choice(matches) if matches else None


class PortraitOverlay(QWidget):
    ui_call_requested = Signal(object)

    def __init__(self, image_dir: Path, host: str, port: int, click_through: bool, state_path: Path) -> None:
        super().__init__()
        self.catalog = PortraitCatalog(image_dir)
        self.current_asset: dict | None = None
        self.current_source_pixmap: QPixmap | None = None
        self.slide_assets = self.catalog.assets[:]
        self.slide_index = 0
        self.is_paused = True
        self.click_through = click_through
        self.scale_factor = 1.0
        self.state_path = state_path
        self.position_restored = False
        self.host = host
        self.port = port
        self.drag_origin: QPoint | None = None
        self.server: ThreadingHTTPServer | None = None
        self.tray: QSystemTrayIcon | None = None
        self.ui_call_requested.connect(lambda callback: callback())
        self._debug_data: dict | None = None
        self._debug_enabled = bool(load_config(Path(__file__).resolve().parent).get("portrait_debug_overlay", False))
        self.load_state()

        self.setWindowTitle("Portrait Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background: transparent;")
        self.image_label.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.image_label)

        self.setLayout(layout)

        # Debug overlay label — absolute-positioned on top of the portrait
        self._debug_label = QLabel(self)
        self._debug_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._debug_label.setStyleSheet(
            "background: rgba(0,0,0,160); color: #0f0; padding: 4px; "
            "font-family: Consolas, monospace; font-size: 12px;"
        )
        self._debug_label.setGeometry(4, 4, 400, 60)
        self._debug_label.setVisible(self._debug_enabled)

        self.slide_timer = QTimer(self)
        self.slide_timer.timeout.connect(self.on_slide_tick)
        self.slide_timer.start(SLIDE_INTERVAL_MS)

        self.create_tray()
        self.show_asset(self.slide_assets[0] if self.slide_assets else None)
        self.show()
        self.apply_native_window_fixes()
        self.apply_click_through_state()
        self.start_control_server()

    def create_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray = QSystemTrayIcon(self)
        icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setToolTip("Portrait Overlay")

        menu = QMenu()
        toggle_passthrough = QAction("Toggle Click Through", self)
        toggle_passthrough.triggered.connect(self.toggle_click_through)
        menu.addAction(toggle_passthrough)

        toggle_play = QAction("Play / Pause", self)
        toggle_play.triggered.connect(self.toggle_pause)
        menu.addAction(toggle_play)

        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        scale = data.get("scale_factor")
        if isinstance(scale, (int, float)):
            self.scale_factor = min(MAX_SCALE, max(MIN_SCALE, float(scale)))

        x = data.get("x")
        y = data.get("y")
        if isinstance(x, int) and isinstance(y, int):
            self.move(x, y)
            self.position_restored = True

    def save_state(self) -> None:
        data = {
            "scale_factor": round(self.scale_factor, 3),
            "x": int(self.x()),
            "y": int(self.y()),
        }
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_tray_tooltip(self, extra: str | None = None) -> None:
        if not self.tray:
            return

        play_state = "paused" if self.is_paused else "playing"
        click_state = "click-through:on" if self.click_through else "click-through:off"
        if extra:
            text = f"{extra} | {play_state} | {click_state} | scale:{self.scale_factor:.1f}x"
        elif not self.current_asset:
            text = f"{play_state} | {click_state}"
        else:
            text = (
                f"{self.current_asset['new_name']} | {self.current_asset['series']} | "
                f"{self.current_asset['pose']} | {self.current_asset['emotion']} | "
                f"{play_state} | {click_state} | scale:{self.scale_factor:.1f}x | "
                f"http://{self.host}:{self.port}"
            )
        self.tray.setToolTip(text)

    def scaled_pixmap(self, pixmap: QPixmap) -> QPixmap:
        width = max(1, int(pixmap.width() * self.scale_factor))
        height = max(1, int(pixmap.height() * self.scale_factor))
        return pixmap.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def refresh_pixmap(self) -> None:
        if self.current_source_pixmap is None:
            return
        scaled = self.scaled_pixmap(self.current_source_pixmap)
        self.image_label.setPixmap(scaled)
        self.image_label.setFixedSize(scaled.size())
        self.resize(scaled.size())
        self.clearMask()
        self.update_tray_tooltip()

    def show_asset(self, asset: dict | None) -> None:
        if not asset:
            self.image_label.clear()
            self.current_source_pixmap = None
            self.update_tray_tooltip("No portrait assets")
            return

        pixmap = QPixmap(asset["path"])
        if pixmap.isNull():
            self.update_tray_tooltip(f"Failed to load {asset['new_name']}")
            return

        self.current_source_pixmap = pixmap
        self.current_asset = asset
        self.refresh_pixmap()

        if not self.position_restored and self.x() == 0 and self.y() == 0:
            screen = QGuiApplication.primaryScreen()
            if screen:
                geometry = screen.availableGeometry()
                self.move((geometry.width() - self.width()) // 2, 40)
                self.save_state()

    def set_current_asset(self, asset: dict) -> None:
        self.is_paused = True
        self.show_asset(asset)
        try:
            self.slide_index = self.slide_assets.index(asset)
        except ValueError:
            pass

    def run_on_ui(self, callback) -> None:
        self.ui_call_requested.emit(callback)

    def on_slide_tick(self) -> None:
        if not self.is_paused and self.slide_assets:
            self.slide_index = (self.slide_index + 1) % len(self.slide_assets)
            self.show_asset(self.slide_assets[self.slide_index])

    def show_next_image(self, manual: bool = False) -> None:
        if not self.slide_assets:
            return
        self.slide_index = (self.slide_index + 1) % len(self.slide_assets)
        self.show_asset(self.slide_assets[self.slide_index])
        if manual:
            self.is_paused = True
            self.update_tray_tooltip()

    def show_previous_image(self) -> None:
        if not self.slide_assets:
            return
        self.slide_index = (self.slide_index - 1) % len(self.slide_assets)
        self.show_asset(self.slide_assets[self.slide_index])
        self.is_paused = True
        self.update_tray_tooltip()

    def toggle_pause(self) -> None:
        self.is_paused = not self.is_paused
        self.update_tray_tooltip()

    def toggle_click_through(self) -> None:
        self.click_through = not self.click_through
        self.apply_click_through_state()
        self.update_tray_tooltip()

    def apply_click_through_state(self) -> None:
        hwnd = int(self.winId())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED
        if self.click_through:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def update_debug(self, data: dict) -> None:
        if not self._debug_enabled:
            return
        self._debug_data = data
        desires = data.get("desires", {})
        thresholds = data.get("thresholds", {})
        cand = data.get("candidates", [])
        lines = [
            f"disturb={data.get('disturbance', 0):.1f}  IDLE={desires.get('IDLE', 0):5.1f} "
            f"MEM={desires.get('MEM', 0):5.1f}  RC={desires.get('RECENT', 0):5.1f}  "
            f"SCR={desires.get('SCREEN', 0):5.1f}",
            f"thr  I={thresholds.get('IDLE', 0):.0f} M={thresholds.get('MEM', 0):.0f} "
            f"R={thresholds.get('RECENT', 0):.0f} S={thresholds.get('SCREEN', 0):.0f}",
            f"candidates: {', '.join(cand) if cand else '—'}",
        ]
        self._debug_label.setText("\n".join(lines))

    def apply_native_window_fixes(self) -> None:
        hwnd = int(self.winId())

        # Force a pure popup-style native window so DWM does not keep a normal rounded frame shell.
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        style |= WS_POPUP
        user32.SetWindowLongW(hwnd, GWL_STYLE, style)

        # Disable non-client rendering and rounded corners on Windows compositor.
        try:
            policy = ctypes.c_int(DWMNCRP_DISABLED)
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_NCRENDERING_POLICY,
                ctypes.byref(policy),
                ctypes.sizeof(policy),
            )
        except Exception:
            pass

        try:
            corner_pref = ctypes.c_int(DWMWCP_DONOTROUND)
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner_pref),
                ctypes.sizeof(corner_pref),
            )
        except Exception:
            pass

        try:
            border_color = ctypes.c_int(0xFFFFFFFE)
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_BORDER_COLOR,
                ctypes.byref(border_color),
                ctypes.sizeof(border_color),
            )
        except Exception:
            pass

    def handle_command(self, payload: dict) -> dict:
        action = payload.get("action", "show")

        if action == "status":
            return {
                "ok": True,
                "current": self.current_asset,
                "series": self.catalog.list_series(),
                "count": len(self.catalog.assets),
            }

        if action == "list":
            filters = {
                "series": payload.get("series"),
                "emotion": payload.get("emotion"),
                "pose": payload.get("pose"),
                "eyes": payload.get("eyes"),
                "mouth": payload.get("mouth"),
            }
            assets = self.catalog.filter_assets(**filters)
            return {"ok": True, "count": len(assets), "assets": assets}

        if action == "show":
            asset = None
            if payload.get("name"):
                asset = self.catalog.get_by_name(payload["name"])
            elif payload.get("random"):
                asset = self.catalog.random_asset(
                    series=payload.get("series"),
                    emotion=payload.get("emotion"),
                    pose=payload.get("pose"),
                    eyes=payload.get("eyes"),
                    mouth=payload.get("mouth"),
                )
            else:
                matches = self.catalog.filter_assets(
                    series=payload.get("series"),
                    emotion=payload.get("emotion"),
                    pose=payload.get("pose"),
                    eyes=payload.get("eyes"),
                    mouth=payload.get("mouth"),
                )
                asset = matches[0] if matches else None

            if not asset:
                return {"ok": False, "error": "No matching portrait"}

            self.run_on_ui(lambda: self.set_current_asset(asset))
            return {"ok": True, "selected": asset}

        if action == "pause":
            self.run_on_ui(lambda: (setattr(self, "is_paused", True), self.update_tray_tooltip()))
            return {"ok": True}

        if action == "play":
            self.run_on_ui(lambda: (setattr(self, "is_paused", False), self.update_tray_tooltip()))
            return {"ok": True}

        if action == "click_through":
            enabled = bool(payload.get("enabled", True))

            def update() -> None:
                self.click_through = enabled
                self.apply_click_through_state()
                self.update_tray_tooltip()

            self.run_on_ui(update)
            return {"ok": True, "enabled": enabled}

        if action == "shutdown":
            self.run_on_ui(self.shutdown)
            return {"ok": True}

        return {"ok": False, "error": f"Unsupported action: {action}"}

    def start_control_server(self) -> None:
        app = self

        class Handler(BaseHTTPRequestHandler):
            def _write_json(self, code: int, data: dict) -> None:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._write_json(200, {"ok": True})
                    return
                if parsed.path == "/status":
                    self._write_json(200, app.handle_command({"action": "status"}))
                    return
                if parsed.path == "/portraits":
                    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    params["action"] = "list"
                    self._write_json(200, app.handle_command(params))
                    return
                self._write_json(404, {"ok": False, "error": "Not found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._write_json(400, {"ok": False, "error": "Invalid JSON"})
                    return

                if parsed.path == "/control":
                    result = app.handle_command(payload)
                    self._write_json(200 if result.get("ok") else 400, result)
                    return
                if parsed.path == "/debug":
                    data = payload.get("data", {})
                    app.run_on_ui(lambda: app.update_debug(data))
                    self._write_json(200, {"ok": True})
                    return
                self._write_json(404, {"ok": False, "error": "Not found"})

            def log_message(self, format: str, *args) -> None:
                return

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.click_through:
            self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self.drag_origin is not None and not self.click_through:
            self.move(event.globalPosition().toPoint() - self.drag_origin)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self.drag_origin = None
        self.save_state()
        event.accept()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0 or self.current_source_pixmap is None:
            event.ignore()
            return

        if delta > 0:
            self.scale_factor = min(MAX_SCALE, round(self.scale_factor + SCALE_STEP, 2))
        else:
            self.scale_factor = max(MIN_SCALE, round(self.scale_factor - SCALE_STEP, 2))

        self.refresh_pixmap()
        self.save_state()
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_F8:
            self.toggle_click_through()
        elif event.key() == Qt.Key.Key_Space:
            self.toggle_pause()
        elif event.key() == Qt.Key.Key_Right:
            self.show_next_image(manual=True)
        elif event.key() == Qt.Key.Key_Left:
            self.show_previous_image()
        elif event.key() == Qt.Key.Key_Escape:
            self.shutdown()

    def shutdown(self) -> None:
        self.save_state()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.tray:
            self.tray.hide()
        self.close()
        QApplication.quit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transparent portrait overlay with local HTTP control.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP bind host")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="HTTP bind port")
    parser.add_argument("--image-dir", default="img", help="Portrait image directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    image_dir = (base_dir / args.image_dir).resolve()
    config = load_config(base_dir)
    click_through = bool(config.get("portrait_click_through", False))
    state_path = base_dir / STATE_FILE

    app = QApplication(sys.argv)
    overlay = PortraitOverlay(image_dir, args.host, args.port, click_through=click_through, state_path=state_path)
    overlay.activateWindow()

    def handle_sigint(_signum, _frame) -> None:
        QTimer.singleShot(0, overlay.shutdown)

    signal.signal(signal.SIGINT, handle_sigint)
    sigint_timer = QTimer()
    sigint_timer.timeout.connect(lambda: None)
    sigint_timer.start(250)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
