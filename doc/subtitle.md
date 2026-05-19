# 字幕覆盖层

实现文件：`kokoro/subtitle.py`

## 作用

独立的透明字幕窗口，用于显示流式 LLM 输出或 STT 识别文本。

辅助场景：
- 桌面模式观察 AI 回复（不看控制台）
- 直播或录屏时让观众看到对话内容
- STT 字幕帮助确认语音识别是否准确

## 架构

与立绘相同模式：主进程 HTTP 控制，子进程 PySide6 透明窗口。

```text
主进程
  │
  └─ SubtitleOverlayClient
       ├─ push_text(text, mode="append"/"set")
       ├─ clear()
       └─ start() / shutdown()
```

## 两个实例

CLI 启动时默认创建两个字幕实例：

| 实例 | 用途 | 颜色 |
|---|---|---|
| chat 字幕 | 显示 AI 回复（流式追加）| 深红描白 |
| STT 字幕 | 显示用户语音识别（覆盖更新）| 深蓝描白 |

独立端口和配置段：

```toml
[subtitle]
font_color = "#8b0000"
stroke_color = "#ffffff"
font_size = 30
subtitle_port = 17353

[subtitle_stt]
font_color = "#00008b"
stroke_color = "#ffffff"
font_size = 30
subtitle_port = 17354
```

## 与立绘的绑定关系

字幕的启停与立绘绑定。`--no-portrait` 时两个字幕也不启动。

## 设计约束

- 字幕属于**纯显示层**，不应反向影响记忆或调度逻辑
- `push_text` 可以追加或覆盖模式
- TTS 播放时字幕与语音同步显示
