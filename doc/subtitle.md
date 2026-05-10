# 字幕覆盖层

字幕覆盖层由 `overlay_subtitle.py` 和 `kokoro/subtitle.py` 实现，用于在桌面上流式显示 LLM 输出文本。

## 配置

```toml
[subtitle]
font_color = "#ffffff"
font_size = 24
subtitle_host = "127.0.0.1"
subtitle_port = 17353
```

## 启动

完整模式 `cli.py` 会在未使用 `--no-portrait` 时启动字幕客户端。

```bash
python cli.py
```

关闭立绘时字幕也不会启动：

```bash
python cli.py --no-portrait
```

## 状态文件

字幕窗口位置和大小保存在：

```text
subtitle_overlay_state.json
```

该文件是运行时状态，不应提交。

## 行为

- LLM streaming 输出时追加显示文本。
- 一轮对话结束后清空字幕。
- 主动搭话结束后也会清空字幕。

## 与 text_cli 的关系

`text_cli.py` 不启动字幕覆盖层。
