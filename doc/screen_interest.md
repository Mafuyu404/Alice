# 屏幕兴趣检测

## 概述

`kokoro/screen_interest.py` 周期性分析屏幕截图，判断当前屏幕内容是否"有趣"，为 proactive 调度器的 SCREEN 行为提供输入。

## 数据流

```
screen_watch 线程 (每 45 秒)
    │
    ├─ 1. 截图 (PIL.ImageGrab)
    ├─ 2. 枚举前台窗口 (win32gui)
    │
    └─ → screen_interest.analyze()
            │
            ├─ 隐私过滤
            │   (密码框、私人聊天、浏览器隐身模式等)
            │
            ├─ vision.detect_desktop()
            │   (截图 + 窗口列表 → 视觉 API)
            │
            └─ 返回 ScreenInterest
                    │
                    ├─ score: 0-100 兴趣度
                    ├─ content: 描述文本
                    ├─ reason: 有趣的理由
                    └─ private: 是否隐私内容
                            │
                            ▼
                    score ≥ interest_threshold (70) 且非隐私
                            │
                            ▼
                    proactive.add_screen_interest(score)
```

## ScreenInterest

```python
@dataclass
class ScreenInterest:
    score: float      # 0-100 兴趣度分数
    content: str      # 屏幕内容描述
    reason: str       # 认为有趣的理由
    private: bool     # 是否隐私内容
```

## 隐私过滤

`PRIVACY_PATTERNS` 列表检测隐私窗口标题关键词，匹配时标记 `private=True` 并跳过：

- 密码管理器
- 私人聊天 / 消息应用
- 浏览器隐身模式
- 网银 / 支付页面
- 密码输入框 / 表单
- 登录页面

`foreground_is_private(title)` 函数返回窗口标题是否匹配隐私模式。

## 内容分析提示词

`_content_prompt()` 使用 `prompts.json` 中的 `screen_interest.content_analysis` 模板，注入前台窗口信息后发送给视觉 API：

- 描述用户当前在做什么
- 判断内容是否有趣
- 给出 0-100 的兴趣度评分
- 说明评分的理由

## 视觉后端

由 `kokoro/vision.py` 实现，支持两种后端：

### DashScope（云端）
使用阿里云 DashScope 视觉模型（如 `qwen-vl-plus`），需要 `vision_api_key`。

### Ollama（本地）
使用 Ollama 的多模态模型，通过 Ollama API 调用。

## 配置

```toml
[screen_watch]
enabled = true
watch_interval = 45.0          # 截图周期（秒）
interest_threshold = 70.0      # 兴趣度阈值
vision_timeout = 45            # 视觉 API 超时（秒）
```

## 禁用

- `--no-screen-watch`：启动时完全禁用屏幕监控
- `[screen_watch] enabled = false`：配置文件中禁用
