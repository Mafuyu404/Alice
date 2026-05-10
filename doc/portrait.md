# 立绘覆盖层

立绘覆盖层由 `overlay_slideshow.py` 和 `kokoro/portrait_controller.py` 实现。

## 配置

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0
portrait_decay_seconds = 60.0
portrait_debug_overlay = false
portrait_click_through = false
portrait_model = "qwen2.5:1.5b"
```

## 角色素材

```text
characters/{id}/portrait/
  portrait.json
  *.png
```

`portrait.json`：

```json
[
  {
    "id": "neutral.png",
    "notes": "平静、正面、适合普通倾听"
  }
]
```

`id` 对应同目录图片文件，`notes` 用于 LLM 选择表情。

## 启动

完整 CLI 默认启动：

```bash
python cli.py
```

关闭：

```bash
python cli.py --no-portrait
```

## 状态文件

窗口位置和缩放保存在：

```text
portrait_overlay_state.json
```

这是运行时文件，不应提交。

## 与字幕

当前 `cli.py` 把字幕客户端和立绘开关联动。使用 `--no-portrait` 时字幕也不会启动。

## 与 text_cli

`text_cli.py` 不启动立绘覆盖层。
