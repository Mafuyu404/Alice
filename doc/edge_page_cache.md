# Edge 网页缓存

`kokoro/edge_cache.py` 通过 Microsoft Edge DevTools Protocol 周期性读取当前 Edge 标签页正文，并覆盖写入同一个缓存文件。

## 配置

```toml
[edge_page_cache]
enabled = true
interval_seconds = 15.0
devtools_host = "127.0.0.1"
devtools_port = 9222
cache_file = "data/edge_page_cache.json"
max_chars = 12000
request_timeout = 3.0
```

## 启动 Edge

必须用远程调试端口启动 Edge：

```powershell
Start-Process msedge -ArgumentList "--remote-debugging-port=9222 --user-data-dir=D:\tmp\alice-edge-debug"
```

如果已有 Edge 正在运行，普通启动命令可能不会打开端口。建议使用独立 `--user-data-dir`。

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:9222/json
```

## 缓存格式

默认文件：

```text
data/edge_page_cache.json
```

内容示例：

```json
{
  "captured_at": "2026-05-11T10:00:00+0800",
  "source": "edge_devtools",
  "foreground": {},
  "tab": {
    "title": "页面标题",
    "url": "https://example.com"
  },
  "text": "网页正文",
  "text_truncated": false
}
```

## 日志

缓存线程会持续覆盖文件，但控制台只在内容变化、标题变化、URL 变化或错误变化时打印日志。重复缓存不会刷屏。

## Impulse 集成

`impulse` 规划时会读取缓存，并把最多约 4000 字网页内容注入规划 prompt。

如果缓存文件记录错误，planner 会看到“Edge 页面缓存不可用”。
