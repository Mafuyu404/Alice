# 屏幕感知

屏幕感知由 `kokoro/screen_interest.py` 和 `kokoro/vision.py` 实现。完整 CLI 可周期性截图并调用视觉模型分析当前桌面内容。

`text_cli.py` 不使用屏幕感知。

## 配置

```toml
vision_backend = "dashscope"
vision_model = "qwen-vl-plus"
vision_api_key = ""
vision_max_pixels = 921600

[screen_watch]
enabled = false
watch_interval = 3.0
interest_threshold = 70.0
vision_timeout = 45
```

## 流程

1. 后台线程定期调用 `screen_interest.analyze()`。
2. `vision.py` 截图并压缩图片。
3. 视觉模型返回内容描述和兴趣分。
4. 结果写入线程安全缓存。
5. 高兴趣内容会进入 `session.screen_contexts`。
6. `impulse` 规划读取缓存。

## 隐私过滤

`screen_interest.foreground_is_private()` 会根据前台窗口信息跳过部分隐私场景。

## 与工具调用

完整 CLI 的 `look_at_screen` 工具会即时截图并分析当前屏幕。它不同于 `screen_watch`：

- `screen_watch` 是后台缓存。
- `look_at_screen` 是模型主动调用工具时即时执行。

## 关闭

配置关闭：

```toml
[screen_watch]
enabled = false
```

命令行关闭：

```bash
python cli.py --no-screen-watch
```
