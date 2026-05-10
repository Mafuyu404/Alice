"""Transparent subtitle overlay — modeled after overlay_slideshow.py."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QPoint, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPixmap,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17353
STATE_FILE = "subtitle_overlay_state.json"
RESIZE_MARGIN = 12
COLLAPSED_WIDTH = 40
COLLAPSED_HEIGHT = 40


class StrokeLabel(QLabel):
    """QLabel that renders text with a colored stroke/outline."""

    def __init__(
        self,
        text_color: str = "#8b0000",
        stroke_color: str = "#ffffff",
        stroke_width: int = 2,
    ) -> None:
        super().__init__()
        self._text_color = QColor(text_color)
        self._stroke_color = QColor(stroke_color)
        self._stroke_width = stroke_width
        self._padding = stroke_width + 6

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        text = self.text()
        if not text:
            return

        p = self._padding
        rect = self.rect().adjusted(p, 2, -p, -2)
        flags = int(self.alignment() | Qt.TextFlag.TextWordWrap)
        font = self.font()
        sw = self._stroke_width

        # Draw outline by rendering text 8 times offset around center
        painter.setPen(self._stroke_color)
        painter.setFont(font)
        offsets = [
            (-sw, -sw), (0, -sw), (sw, -sw),
            (-sw, 0),             (sw, 0),
            (-sw, sw),  (0, sw),  (sw, sw),
        ]
        for dx, dy in offsets:
            painter.save()
            painter.translate(dx, dy)
            painter.drawText(rect, flags, text)
            painter.restore()

        # Draw fill text on top
        painter.setPen(self._text_color)
        painter.drawText(rect, flags, text)


class ResizeHandle(QWidget):
    """Bottom-right resize grip with visible bars and drag-to-resize."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._active = False
        self._origin: QPoint | None = None
        self._geom: tuple | None = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Softer background for clickable area
        bg = QColor("#888888")
        bg.setAlpha(40)
        painter.fillRect(self.rect(), bg)
        # Draw three diagonal bars as grip indicator
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        for i in range(3):
            x = self.width() - 8 - i * 6
            y = self.height() - 8 - i * 6
            painter.drawRoundedRect(x, y, 10, 3, 1.5, 1.5)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active = True
            self._origin = event.globalPosition().toPoint()
            p = self.parent()
            self._geom = (p.x(), p.y(), p.width(), p.height())
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._active and self._origin and self._geom:
            delta = event.globalPosition().toPoint() - self._origin
            x, y, w, h = self._geom
            self.parent().setGeometry(x, y, max(100, w + delta.x()), max(60, h + delta.y()))

    def mouseReleaseEvent(self, event) -> None:
        if self._active:
            self._active = False
            self._origin = None
            p = self.parent()
            if hasattr(p, 'save_state'):
                p.save_state()
            event.accept()


class SubtitleOverlay(QWidget):
    ui_call_requested = Signal(object)

    def __init__(self, host: str, port: int, font_color: str, font_size: int, stroke_color: str, btn_color: str = "#8b0000", state_file: str = STATE_FILE) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.font_color = font_color
        self.font_size = font_size
        self.stroke_color = stroke_color
        self.btn_color = btn_color
        self.is_collapsed = False
        self.server: ThreadingHTTPServer | None = None
        self.tray: QSystemTrayIcon | None = None
        self._drag_origin: QPoint | None = None
        self._drag_frame: QPoint | None = None
        self._resize_edge = 0
        self._resize_origin: QPoint | None = None
        self._resize_geom: tuple = (0, 0, 0, 0)
        self.state_path = Path(__file__).resolve().parent / state_file

        self.ui_call_requested.connect(lambda cb: cb())

        self.setWindowTitle("Subtitle Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("SubtitleOverlay { background: transparent; }")
        self.setMouseTracking(True)

        # text label with stroke
        self.text_label = StrokeLabel(
            text_color=font_color,
            stroke_color=stroke_color,
        )
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text_label.setText("")
        text_font = QFont()
        text_font.setPixelSize(font_size)
        text_font.setBold(True)
        self.text_label.setFont(text_font)

        # toggle button
        self.toggle_btn = QPushButton("▼")
        self.toggle_btn.setFixedSize(24, 24)
        self.toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                font-size: 16px;
                color: {self.btn_color};
            }}
            QPushButton:hover {{ color: #ffffff; }}
        """)
        self.toggle_btn.clicked.connect(self.toggle_collapse)
        self.toggle_btn.mousePressEvent = self._btn_press
        self.toggle_btn.mouseMoveEvent = self._btn_move
        self.toggle_btn.mouseReleaseEvent = self._btn_release

        # layout
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 4, 0)
        top_bar.addStretch()
        top_bar.addWidget(self.toggle_btn)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addLayout(top_bar)
        main_layout.addWidget(self.text_label, 1)
        self.setLayout(main_layout)

        # resize handle — visible grip at bottom-right
        self.size_grip = ResizeHandle(self)

        # Force initial position: bottom-center of primary screen
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self._expanded_width = 500
            self._expanded_height = 160
            self.move((geo.width() - 500) // 2, geo.height() - 220)

        self.load_state()
        self._apply_size()

        self.create_tray()
        self.start_control_server()

    def _apply_size(self) -> None:
        if self.is_collapsed:
            self.resize(COLLAPSED_WIDTH, COLLAPSED_HEIGHT)
        else:
            self.resize(self._expanded_width, self._expanded_height)

    def toggle_collapse(self) -> None:
        self.is_collapsed = not self.is_collapsed
        if self.is_collapsed:
            self._expanded_width = self.width()
            self._expanded_height = self.height()
            self.text_label.setVisible(False)
            self.size_grip.setVisible(False)
            self.toggle_btn.setText("▶")
        else:
            self.text_label.setVisible(True)
            self.size_grip.setVisible(True)
            self.toggle_btn.setText("▼")
        self._apply_size()
        self.save_state()

    # text API

    def set_text(self, text: str) -> None:
        self.text_label.setText(text)

    def append_text(self, text: str) -> None:
        current = self.text_label.text()
        self.text_label.setText(current + text)

    def clear_text(self) -> None:
        self.text_label.setText("")

    # drag / resize

    def _edge_at(self, pos: QPoint) -> int:
        if self.is_collapsed:
            return 0
        edge = 0
        if pos.x() <= RESIZE_MARGIN:
            edge |= 1
        if pos.x() >= self.width() - RESIZE_MARGIN:
            edge |= 2
        if pos.y() <= RESIZE_MARGIN:
            edge |= 4
        if pos.y() >= self.height() - RESIZE_MARGIN:
            edge |= 8
        return edge

    def _btn_press(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._drag_frame = event.globalPosition().toPoint()
            event.accept()

    def _btn_move(self, event) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()

    def _btn_release(self, event) -> None:
        if self._drag_origin is not None:
            # Click if finger didn't move much (5px threshold)
            if self._drag_frame and (event.globalPosition().toPoint() - self._drag_frame).manhattanLength() < 5:
                self.toggle_collapse()
            self._drag_origin = None
            self._drag_frame = None
            self.save_state()
            event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        g = self.size_grip
        g.move(self.width() - g.width(), self.height() - g.height())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_geom = (self.x(), self.y(), self.width(), self.height())
                return

    def mouseMoveEvent(self, event) -> None:
        if self._resize_edge:
            delta = event.globalPosition().toPoint() - self._resize_origin
            x, y, w, h = self._resize_geom
            if self._resize_edge & 1:
                x += delta.x(); w -= delta.x()
            if self._resize_edge & 2:
                w += delta.x()
            if self._resize_edge & 4:
                y += delta.y(); h -= delta.y()
            if self._resize_edge & 8:
                h += delta.y()
            self.setGeometry(max(0, x), max(0, y), max(100, w), max(60, h))
            return
        # Show resize cursor at window edges
        if self.is_collapsed:
            return
        pos = event.position().toPoint()
        edge = self._edge_at(pos)
        cursors = {
            1: Qt.CursorShape.SizeHorCursor,   # left
            2: Qt.CursorShape.SizeHorCursor,   # right
            4: Qt.CursorShape.SizeVerCursor,   # top
            8: Qt.CursorShape.SizeVerCursor,   # bottom
            5: Qt.CursorShape.SizeFDiagCursor, # top-left
            6: Qt.CursorShape.SizeBDiagCursor, # top-right
            9: Qt.CursorShape.SizeBDiagCursor, # bottom-left
            10: Qt.CursorShape.SizeFDiagCursor, # bottom-right
        }
        cursor = cursors.get(edge)
        if cursor is not None:
            self.setCursor(cursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event) -> None:
        if self._resize_edge:
            self._resize_edge = 0
            self.save_state()

    # tray

    def create_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self)
        icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setToolTip("Subtitle Overlay")
        menu = QMenu()
        toggle_collapse = QAction("Collapse / Expand", self)
        toggle_collapse.triggered.connect(self.toggle_collapse)
        menu.addAction(toggle_collapse)
        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

    # state persistence

    def load_state(self) -> None:
        self._expanded_width = 400
        self._expanded_height = 120
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if "expanded_width" in data:
                    self._expanded_width = data["expanded_width"]
                if "expanded_height" in data:
                    self._expanded_height = data["expanded_height"]
                x, y = data.get("x"), data.get("y")
                if isinstance(x, int) and isinstance(y, int) and x >= 0 and y >= 0:
                    self.move(x, y)
                    return
            except (OSError, json.JSONDecodeError):
                pass
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move((geo.width() - self._expanded_width) // 2, geo.height() - self._expanded_height - 60)

    def save_state(self) -> None:
        if self.is_collapsed:
            w, h = self._expanded_width, self._expanded_height
        else:
            w, h = self.width(), self.height()
            self._expanded_width = w
            self._expanded_height = h
        data = {
            "x": self.x(),
            "y": self.y(),
            "expanded_width": w,
            "expanded_height": h,
        }
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # HTTP control server

    def handle_command(self, payload: dict) -> dict:
        action = payload.get("action", "")
        if action == "text":
            text = payload.get("text", "")
            mode = payload.get("mode", "set")
            if mode == "append":
                self.run_on_ui(lambda: self.append_text(text))
            else:
                self.run_on_ui(lambda: self.set_text(text))
            return {"ok": True}
        if action == "clear":
            self.run_on_ui(self.clear_text)
            return {"ok": True}
        if action == "show":
            self.run_on_ui(self.show)
            return {"ok": True}
        if action == "hide":
            self.run_on_ui(self.hide)
            return {"ok": True}
        if action == "shutdown":
            self.run_on_ui(self.shutdown)
            return {"ok": True}
        if action == "status":
            return {
                "ok": True,
                "visible": self.isVisible(),
                "collapsed": self.is_collapsed,
                "text": self.text_label.text(),
            }
        return {"ok": False, "error": f"Unknown action: {action}"}

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
                self._write_json(404, {"ok": False})

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
                self._write_json(404, {"ok": False})

            def log_message(self, fmt, *args) -> None:
                return

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def run_on_ui(self, callback) -> None:
        self.ui_call_requested.emit(callback)

    def shutdown(self) -> None:
        self.save_state()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.tray:
            self.tray.hide()
        self.close()
        QApplication.quit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transparent subtitle overlay.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP bind host")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="HTTP bind port")
    parser.add_argument("--font-color", default="#8b0000", help="Text color")
    parser.add_argument("--stroke-color", default="#ffffff", help="Text stroke color")
    parser.add_argument("--font-size", default=24, type=int, help="Font size")
    parser.add_argument("--btn-color", default="#8b0000", help="Toggle button color")
    parser.add_argument("--state-file", default=STATE_FILE, help="State file name (e.g. subtitle_state.json)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = QApplication(sys.argv)
    overlay = SubtitleOverlay(args.host, args.port, args.font_color, args.font_size, args.stroke_color, btn_color=args.btn_color, state_file=args.state_file)
    overlay.show()

    def handle_sigint(_signum, _frame) -> None:
        QTimer.singleShot(0, overlay.shutdown)

    signal.signal(signal.SIGINT, handle_sigint)
    sigint_timer = QTimer()
    sigint_timer.timeout.connect(lambda: None)
    sigint_timer.start(250)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
