#!/usr/bin/env python3
"""Read-only web viewer for mem0 memories."""

from __future__ import annotations

import argparse
import html
import json
import os
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from kokoro import config as cfg
from kokoro import memory as mem_mod


CONFIG = cfg.load()
BACKEND = mem_mod.create_backend(CONFIG)


def _character_ids() -> list[str]:
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "characters")
    if not os.path.isdir(root):
        return ["default"]
    ids = [
        name
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name))
    ]
    return sorted(ids) or ["default"]


CHARACTER_IDS = _character_ids()


class MemoryViewerHandler(BaseHTTPRequestHandler):
    server_version = "AliceMemoryViewer/1.0"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/memories":
            self._api_memories(parsed)
            return
        if parsed.path in ("/", "/index.html"):
            self._html()
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:
        return

    def _api_memories(self, parsed) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        user_id = (query.get("user_id") or [CHARACTER_IDS[0]])[0]
        try:
            limit = int((query.get("limit") or ["200"])[0])
        except ValueError:
            limit = 200
        items = BACKEND.list_memories(user_id=user_id, limit=max(1, min(limit, 1000)))
        payload = {
            "ready": bool(getattr(BACKEND, "ready", False)),
            "user_id": user_id,
            "count": len(items),
            "items": items,
        }
        self._json(payload)

    def _json(self, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self) -> None:
        options = "\n".join(
            f'<option value="{html.escape(cid)}">{html.escape(cid)}</option>'
            for cid in CHARACTER_IDS
        )
        page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Alice Memory Viewer</title>
  <style>
    :root {{ color-scheme: light dark; font-family: "Segoe UI", Arial, sans-serif; }}
    body {{ margin: 0; background: #f6f7f8; color: #171a1f; }}
    header {{ position: sticky; top: 0; background: #ffffff; border-bottom: 1px solid #d8dde5; padding: 14px 22px; display: flex; gap: 12px; align-items: center; }}
    h1 {{ font-size: 18px; margin: 0 14px 0 0; font-weight: 650; }}
    select, input, button {{ height: 34px; border: 1px solid #b8c0cc; border-radius: 6px; padding: 0 10px; background: #fff; color: #171a1f; }}
    button {{ cursor: pointer; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 18px 22px 40px; }}
    .meta {{ color: #596273; margin-bottom: 12px; }}
    .empty {{ padding: 28px; border: 1px dashed #b8c0cc; border-radius: 8px; background: #fff; color: #596273; }}
    .memory {{ background: #fff; border: 1px solid #d8dde5; border-radius: 8px; padding: 14px 16px; margin: 10px 0; }}
    .memory .top {{ display: flex; gap: 10px; align-items: baseline; color: #596273; font-size: 12px; margin-bottom: 8px; flex-wrap: wrap; }}
    .id {{ font-family: ui-monospace, Consolas, monospace; color: #394150; }}
    .text {{ white-space: pre-wrap; line-height: 1.55; font-size: 15px; }}
    .error {{ color: #a12622; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #14171c; color: #e8ecf2; }}
      header, .memory, .empty {{ background: #1b2028; border-color: #313946; }}
      select, input, button {{ background: #202733; color: #e8ecf2; border-color: #4a5363; }}
      .meta, .memory .top {{ color: #a8b1c2; }}
      .id {{ color: #c2cad8; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Alice Memory Viewer</h1>
    <label>角色 <select id="user">{options}</select></label>
    <label>数量 <input id="limit" type="number" min="1" max="1000" value="200"></label>
    <button id="refresh">刷新</button>
  </header>
  <main>
    <div id="meta" class="meta">加载中...</div>
    <div id="list"></div>
  </main>
  <script>
    const user = document.querySelector('#user');
    const limit = document.querySelector('#limit');
    const meta = document.querySelector('#meta');
    const list = document.querySelector('#list');
    document.querySelector('#refresh').addEventListener('click', load);
    user.addEventListener('change', load);

    function esc(value) {{
      return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}

    async function load() {{
      meta.textContent = '加载中...';
      list.innerHTML = '';
      try {{
        const params = new URLSearchParams({{user_id: user.value, limit: limit.value || '200'}});
        const resp = await fetch('/api/memories?' + params.toString());
        const data = await resp.json();
        if (!data.ready) {{
          meta.innerHTML = '<span class="error">记忆后端不可用。请确认 memory_backend = "mem0" 且 mem0 初始化成功。</span>';
          return;
        }}
        meta.textContent = `${{data.user_id}}：${{data.count}} 条记忆`;
        if (!data.items.length) {{
          list.innerHTML = '<div class="empty">没有记忆。</div>';
          return;
        }}
        list.innerHTML = data.items.map(item => `
          <section class="memory">
            <div class="top">
              <span class="id">${{esc(item.id)}}</span>
              <span>创建：${{esc(item.created_at || '-')}}</span>
              <span>更新：${{esc(item.updated_at || '-')}}</span>
            </div>
            <div class="text">${{esc(item.memory)}}</div>
          </section>
        `).join('');
      }} catch (err) {{
        meta.innerHTML = '<span class="error">加载失败：' + esc(err) + '</span>';
      }}
    }}
    load();
  </script>
</body>
</html>"""
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only mem0 memory viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17410)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MemoryViewerHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"[memory_viewer] {url}")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[memory_viewer] stopping")
    finally:
        server.server_close()
        BACKEND.close()


if __name__ == "__main__":
    main()
