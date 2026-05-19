# Edge 页面缓存

实现文件：`kokoro/edge_cache.py`

## 作用

通过 Microsoft Edge DevTools Protocol 周期性读取当前前台 Edge 标签页的正文内容，写入缓存文件，供调度器作为网页上下文使用。

## 工作原理

```text
Edge 浏览器 (--remote-debugging-port=9222)
  │
  ▼ (HTTP: GET /json → tab 列表)
edge_cache.list_tabs()
  │
  ├─ 匹配前台窗口（按进程名 + 标题归一化）
  ├─ 或匹配上一个缓存的 tab（按 id → url → 默认第一个）
  └─ 选择 tab
  │
  ▼ (WebSocket: Runtime.evaluate → document.body.innerText)
edge_cache.capture_current_page()
  │
  ├─ 注入 JS 表达式获取 title + url + innerText
  ├─ 截断到 max_chars
  └─ 写入缓存文件 (data/edge_page_cache.json)
  │
  ▼ (覆盖写入，其他进程可随时读)
edge_cache.format_for_prompt()
  └─ 格式化: 标题 + URL + 抓取时间 + 正文
```

## 缓存文件格式

```json
{
  "captured_at": "2026-01-15T14:30:00+0800",
  "source": "edge_devtools",
  "foreground": { "title": "...", "process": "msedge.exe", "pid": 1234 },
  "tab": { "id": "...", "title": "...", "url": "..." },
  "text": "页面正文...",
  "text_truncated": false
}
```

## 使用场景

### 单角色场景

调度器在空闲检查时读取缓存，判断当前网页内容是否值得角色评论。

### 多人随机 MC 页面讲解

启用 `random_mc_enabled = true` 时：
- 页面切换时触发 `random_mc_page_changed` 事件
- 调度器强制转向新页面讨论
- idle tick 不轻易沉默——维持 MC 页面讲解活跃度

### 事实锚定规则

- 页面缓存只提供事实材料
- 页面无内容时直接说"信息不够"
- 不允许凭想象补全页面内容
- 角色可以有态度和推测，但具体事实必须来自缓存

## 配置

```toml
[edge_page_cache]
enabled = true
interval_seconds = 1.0
devtools_host = "127.0.0.1"
devtools_port = 9222
cache_file = "data/edge_page_cache.json"
max_chars = 12000
request_timeout = 3.0
```

## 启动 Edge

```bat
msedge.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\alice-edge-debug"
```

推荐使用独立的 user-data-dir，避免与日常浏览冲突。

## 常见问题

### "Cannot connect to Edge DevTools"
Edge 没有以调试端口启动，或端口不对。

### 页面内容为空
部分页面（设置页、新标签页、某些插件页面）不支持 DevTools 注入 JS。这些页面不会产生可以读的网页缓存。

### 缓存脏读
写入是原子替换（先写 `.tmp` 再 `os.replace`），读时不会读到半写状态。
