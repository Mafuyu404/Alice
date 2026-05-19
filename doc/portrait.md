# 立绘覆盖层

## 架构

立绘系统由两个进程组成：

```text
主进程 (cli.py)
  │
  ├─ PortraitOverlayClient → HTTP 控制 overlay_slideshow.py
  │     ├─ show(name) → 切换到指定立绘
  │     ├─ pause()    → 暂停轮播
  │     ├─ status()   → 当前显示状态
  │     └─ shutdown() → 关闭 overlay 窗口
  │
  └─ PortraitDecisionWorker → 后台线程
        ├─ 监听 dialogue/impulse 产出的回复
        ├─ LLM 选择最适合当前语境的立绘
        └─ → client.show(selected_id)

子进程 (overlay_slideshow.py)
  │
  └─ PySide6 透明窗口
       ├─ 显示立绘图片
       ├─ 支持多角色并排 (slot)
       └─ HTTP API 控制
```

## 立绘配置

立绘配置在 `characters/{id}/portrait/portrait.json` 或全局 `characters/portraits.json`：

```json
[
  { "id": "neutral", "notes": "平静表情" },
  { "id": "happy", "notes": "微笑，看起来心情不错" },
  { "id": "thinking", "notes": "稍显困惑或正在思考" }
]
```

`notes` 字段很重要——它作为 LLM 选择时的描述依据。

## 表情选择流程

`PortraitDecisionWorker` 后台循环：

```text
_loop() [每 ~2 秒]
  │
  ├─ [条件] 空闲超过 decay_seconds (60s)
  │   └─ → 恢复到 neutral 平静表情
  │
  └─ [条件] 有新的 dialogue 回复 (submit 被调用)
      │
      └─ _decide(user_text, assistant_text, idle_time)
           │
           ├─ system: "你是立绘选择器..."
           ├─ user: 当前立绘 + 对话内容 + 候选立绘目录
           ├─ → LLM 流式输出
           └─ → 匹配 valid_ids 中的 id
```

### LLM 提示词

```text
system: "你是立绘选择器。根据最近一轮用户输入和角色回复，
         从候选立绘中选择最合适的一张。只能输出一个候选 id，
         不要解释。如果当前立绘已经合适，可以输出当前 id。"

user: "当前立绘：{current_id}
       {user_name}：{user_text}
       {name}：{assistant_text}
       [可选: 距离上次对话已经过去 X 秒]

       候选立绘：
       - neutral: 平静表情
       - happy: 微笑，看起来心情不错
       ...

       输出一个 id："
```

### 衰减机制

- `portrait_decay_seconds` 秒无对话后自动回到 `neutral`
- 这是独立于 LLM 选择器的规则，确保长时间静默后立绘不卡在奇怪的表情上

## 多角色立绘

多人模式下：

- 每个角色有独立的 overlay 窗口
- 端口从 `portrait_overlay_port + 1` 开始分配
- `slot_index` / `slot_count` 控制窗口在屏幕上的位置排列
- 每个角色有自己的 `PortraitOverlayClient` + `PortraitDecisionWorker`
- `state_file` 各自独立

## 配置

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0   # 0 = 使用后端默认
portrait_decay_seconds = 60.0
portrait_model = "deepseek-v4-flash"
```

`portrait_model` 独立配置：如果有角色使用 charglm 等非对话模型，这里必须显式指定，否则立绘选择会走到错误的模型。
