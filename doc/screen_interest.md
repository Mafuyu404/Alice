# 屏幕兴趣度

实现文件：`kokoro/screen_interest.py`

## 作用

周期性截取桌面截图，通过视觉 API 分析内容，判断是否值得角色主动评论。

**不负责**：决定是否开口——那个是 `DialogueOrchestrator` 的事。

## 分析流程

```text
screen_interest.analyze()
  │
  ├─ 截取全屏 (PIL.ImageGrab)
  │   └─ 必要时等比缩小 (默认上限 921600 像素)
  │
  ├─ 获取前台窗口信息
  │
  ├─ 拼装分析提示词
  │   └─ 包含 fg_info + "请完成两件事：描述内容 + 判断是否适合评论"
  │
  ├─ 调用视觉 API
  │   ├─ DashScope qwen-vl-plus（推荐，云端）
  │   └─ Ollama 多模态模型（本地备选）
  │
  └─ 解析 LLM JSON 返回
      ├─ score: 0-100
      ├─ content: 内容描述
      ├─ reason: 评论理由
      └─ private: 是否隐私
```

## 输出格式

```json
{
  "score": 0,
  "content": "对前台窗口和用户当前操作的简要描述",
  "reason": "为什么适合或不适合评论",
  "private": false
}
```

评分标准：
- 70+：有明确可读内容或清晰任务上下文，适合评论
- 30-70：有一些内容但上下文不够明确
- 30-：空白、纯装饰、隐私或没有可读内容

## 隐私检测

- 登录、密码、支付、银行、医疗、私人聊天或会议 → `private=true, score=0`
- 隐私内容不进入对话上下文，也不触发主动搭话

## 缓存

`screen_interest` 维护一个全局缓存（`ScreenInterestCache`），最新分析结果始终可用。

谁读缓存：
- `DialogueOrchestrator._cache_overview_for_planner()` — 检查是否值得主动搭话
- 旧 `impulse` 模块（已废弃）

谁写缓存：
- `screen_cache_worker`（cli.py 后台线程）

## 后端配置

```toml
vision_backend = "dashscope"     # dashscope / ollama
vision_model = "qwen-vl-plus"    # DashScope
vision_api_key = ""               # DashScope key
vision_max_pixels = 921600       # 截图缩放上限
```

## 与对话调度器的关系

```text
screen_cache_worker (每 watch_interval 秒)
  │
  ├─ screen_interest.analyze() → 写入缓存
  │
  ▼
_dialogue_context_worker (每 30s)
  │
  └─ 读缓存 → DialogueOrchestrator.decide(event=context_cache)
       └─ 只有 score >= context_idle_min_score 才作为候选
```
