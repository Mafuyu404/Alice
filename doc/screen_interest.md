# 屏幕兴趣检测

## 概述

`kokoro/screen_interest.py` 周期性分析屏幕截图，判断内容是否"有趣"，输出给 proactive 调度器的 SCREEN 行为，同时也通过 `session.add_screen_context()` 注入对话历史。

## 数据流

```
screen_watch 线程 (每 watch_interval 秒)
    │
    ├─ 1. 截图 (PIL.ImageGrab)
    ├─ 2. 枚举前台窗口 (win32gui)
    │
    └─ → screen_interest.analyze()
            │
            ├─ 隐私过滤 (PRIVACY_PATTERNS)
            │   private=True → 跳过
            │
            ├─ vision.analyze_image()
            │   (截图 + content_analysis 提示词 → 视觉 API)
            │
            └─ 解析 JSON 返回 ScreenInterest
                    │
                    ├─ score: 0-100 兴趣度
                    ├─ content: 对前台窗口的详尽描述（含所有可见文本、UI 布局、
                    │            视觉元素、颜色风格、用户操作状态等）
                    ├─ reason: 有趣的理由
                    └─ private: 是否隐私内容
                            │
                score ≥ interest_threshold 且非 private
                            │
                            ▼
                    ├─→ scheduler.add_screen_interest(score, context)
                    └─→ session.add_screen_context(content)
```

## ScreenInterest

```python
@dataclass(frozen=True)
class ScreenInterest:
    score: float      # 0-100 兴趣度
    content: str      # 详细描述（含文本、布局、UI 状态等）
    reason: str       # 认为有趣的理由
    private: bool     # 是否隐私内容
```

## 隐私过滤

`PRIVACY_PATTERNS` 列表匹配窗口标题关键词，匹配时标记 `private=True`：

- 密码/登录/支付/银行页面
- 私人聊天/会议软件（Zoom、Teams 等）
- 浏览器隐身模式
- 验证码/2FA 页面

`foreground_is_private(foreground)` 函数返回窗口标题是否匹配隐私模式。

## 内容分析提示词

`_content_prompt()` 使用 `prompts.json` 中的 `screen_interest.content_analysis` 模板，要求视觉 API 输出包含以下维度的 JSON：

- 窗口标题、界面布局（区域划分）
- 所有可见文本（按钮、标签、对话框、列表项、数值、状态信息）
- UI 元素状态（选中项、进度条、开关、高亮项）
- 图像/图标/视觉元素描述
- 颜色和视觉风格
- 用户可能的操作状态
- 动画或动态效果
- 窗口外桌面区域

## 视觉后端

由 `kokoro/vision.py` 实现，支持两种后端：

- **DashScope**（云端）：阿里云视觉模型 `qwen-vl-plus` / `qwen-vl-max`，需要 API Key，识别能力强
- **Ollama**（本地）：Ollama 多模态模型 `llava` 等，免费但识别能力有限

## 配置

```toml
[screen_watch]
enabled = true
watch_interval = 45.0          # 截图周期（秒）
interest_threshold = 70.0      # 兴趣度阈值（0-100）
vision_timeout = 45            # 视觉 API 超时（秒）
memory_events_enabled = true   # 记忆事件轮询开关
memory_check_interval = 300.0  # 记忆事件轮询间隔
memory_cooldown_seconds = 21600.0  # 记忆事件冷却（6 小时）
memory_date_score = 50.0       # 日期匹配基准分
memory_lookup_score = 70.0     # 记忆查询基准分
memory_lookup_query = "recent important user preferences, plans, dates, anniversaries, goals"
```

## 禁用

- `--no-screen-watch`：启动时完全禁用屏幕监控
- `[screen_watch] enabled = false`：配置文件中禁用
