# 屏幕兴趣检测

## 概述

`kokoro/screen_interest.py` 周期性分析屏幕截图，判断内容是否"有趣"，输出给 proactive 调度器的 SCREEN 行为，同时也通过 `session.add_screen_context()` 注入对话历史。

## 数据流

```
screen_watch 线程 (每 watch_interval 秒)
    │
    ├─ 1. vision.get_foreground_app() — 获取前台窗口信息
    ├─ 2. vision.screenshot_to_base64() — 全屏截图转 base64
    │
    └─ → screen_interest.analyze()
            │
            ├─ foreground_is_private(foreground) — 隐私过滤
            │   private=True → 返回 ScreenInterest(private=True)
            │
            ├─ _content_prompt(foreground) — 构建分析提示词
            │   (含前台窗口标题 + 进程名)
            │
            ├─ vision.analyze_image() — 调用视觉 API
            │
            └─ _parse_content() — 解析 JSON 响应
                    │
                    ├─ score: 0-100 兴趣度
                    ├─ content: 对前台窗口的详尽描述（截断至 600 字符）
                    ├─ reason: 有趣的理由（截断至 200 字符）
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
    content: str      # 详细描述
    reason: str       # 认为有趣的理由
    private: bool     # 是否隐私内容
```

## 隐私过滤

`foreground_is_private(foreground)` 检查前台窗口的标题、进程名、窗口类名是否匹配隐私关键词：

```
password, passwd, login, sign in, signin, bank, payment, checkout, wallet,
authenticator, 2fa, private browsing, incognito, 隐私, 密码, 登录, 登陆,
支付, 付款, 银行, 验证码, 会议, meeting, zoom, teams, tencentmeeting
```

匹配时返回 `ScreenInterest(private=True)`，CLI 端设置 `quiet_until` 推迟下次检查。

## 内容分析提示词

`_content_prompt()` 使用 `prompts.json` 中的 `screen_interest.content_analysis` 模板，注入前台窗口标题和进程名作为 `{fg_info}`。要求视觉 API 输出包含以下维度的 JSON：

- 窗口标题、界面布局（区域划分）
- 所有可见文本（按钮、标签、对话框、列表项、数值、状态信息）
- UI 元素状态（选中项、进度条、开关、高亮项）
- 图像/图标/视觉元素描述
- 颜色和视觉风格
- 用户可能的操作状态
- 动画或动态效果
- 窗口外桌面区域

## JSON 解析

`_extract_json()` 使用三重策略提取视觉模型返回的 JSON：

1. Markdown code block 提取（```` ```json ... ``` ````）
2. 最外层平衡大括号匹配
3. 清理尾逗号后重试

解析失败时返回原始文本（score=0）。

## 视觉后端

由 `kokoro/vision.py` 实现，支持两种后端：

- **DashScope**（云端）：阿里云视觉模型 `qwen-vl-plus` / `qwen-vl-max`，需要 API Key
- **Ollama**（本地）：Ollama 多模态模型 `qwen2.5vl:3b` 等，免费但识别能力有限

详见 [vision.md](#)（`kokoro/vision.py` 还提供 `get_running_apps()`、`get_foreground_app()`、`detect_desktop()` 等窗口枚举和综合桌面分析功能）。

## CLI 主循环集成

`cli.py` 中 `screen_watch_worker` 线程的工作流程：

1. 等待 `screen_watch_interval` 秒
2. 检查聊天锁/TTS 播放状态（忙时跳过）
3. 调用 `begin_screen_watch()` 获取唯一 ID（支持取消）
4. 调用 `screen_interest.analyze()`
5. 检查是否被用户命令取消（`consume_screen_watch_canceled()`）
6. 结果处理：
   - `private=True` → 设置 `quiet_until` 推迟下次检查
   - `score >= interest_threshold` → 注入 scheduler + session

## 配置

```toml
[screen_watch]
enabled = true
watch_interval = 45.0          # 截图周期（秒），最小 10
interest_threshold = 70.0      # 兴趣度阈值（0-100）
vision_timeout = 45            # 视觉 API 超时（秒），最小 5
```

记忆事件配置已移至 `[proactive]` 节下，详见 [memory.md](memory.md)。

## 禁用

- `--no-screen-watch`：启动时完全禁用屏幕监控
- `[screen_watch] enabled = false`：配置文件中禁用
