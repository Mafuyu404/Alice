# 文本 CLI

入口文件：`text_cli.py`

用途：

- 调试人格
- 调试提示词
- 回归测试
- 调试记忆、认知、情绪

## 常用命令

```bash
python text_cli.py
python text_cli.py --no-memory --no-store --no-cognition
python text_cli.py --tools
python text_cli.py --read-only-tools
```

## 特点

- 无语音链路
- 无立绘和字幕
- 启动快
- 更适合稳定复现
- 文件工具默认关闭，避免普通对话测试误触发项目文件读取

## 适合场景

- 调整角色说话风格
- 验证 memory / cognition 是否跑偏
- 验证多轮对话是否稳定
