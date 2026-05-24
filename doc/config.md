# 配置说明

## 配置文件层级

| 文件 | 用途 | 提交 |
|---|---|---|
| `config.toml` | 主配置，所有可调参数 | 可以 |
| `config.json` | 本地密钥和局部覆盖，与 toml 同结构 | 不可以 |

优先级：`config.toml` > `config.json`。toml 中已设置的值不会被 json 覆盖，只填充 toml 中为空的项。

配置加载入口：`kokoro/config.py` 中的 `load()`，合并逻辑见 `_merge_fallback()`。

---

## 完整配置参考

### 用户身份

```toml
# AI 对用户的称呼。更换后记忆和认知会以新名字重新索引。
user_name = "真冬"
```

### 场景开关

```toml
[scene]
# 是否按多人场景理解说话人（每条消息前标注名字）
multi_enabled = false
# 是否按直播场景理解弹幕/观众输入
live_enabled = false
# 是否按"随机 MC 百科页面讲解"理解当前网页缓存
random_mc_enabled = true
```

三个开关各自独立，可以组合。例如 `multi = true, live = true` = 多人直播。

场景影响 `ChatSession.scene_guidance` 的注入内容，以及调度器的上下文使用策略。

### LLM 路由

```toml
# 基础 LLM 地址（Ollama / 兼容服务）
llm_url = "http://127.0.0.1:11434"

# 默认对话模型
llm_model = "deepseek-v4-flash"

# 主对话生成模型（留空复用 llm_model）
dialogue_model = "deepseek-v4-flash"

# 主动搭话/调度用的 planner 模型
impulse_model = "deepseek-v4-flash"

# DeepSeek API 密钥（模型名以 deepseek 开头时自动路由）
deepseek_api_key = ""

# 智谱 CharGLM API 密钥（使用 charglm 模型时需要）
charglm_api_key = ""
```

路由逻辑：
- 模型名以 `deepseek` 开头 → 自动使用 `api.deepseek.com`，需要 `deepseek_api_key`
- 模型名以 `charglm` 开头 → 使用 `charglm_api_key`，URL 从角色配置或全局 `llm_url`
- 其他 → 使用 `llm_url`，不需要 key

### 对话调度器

```toml
[dialogue]
# 总开关
enabled = true

# planner 模型。留空则回退 impulse.planning_model → impulse_model → llm_model
planning_model = ""

# 打印 planner 决策日志
log_decisions = true

# planner 可见的最近消息数
max_recent_messages = 10

# planner 可见的角色 system prompt 最大字符（截断以节约 token）
max_character_prompt_chars = 900

# planner 可见的角色卡单字段最大字符
max_profile_field_chars = 420

# schedule 动作的最大延迟秒数
max_delay_seconds = 120.0

# 空闲时检查主动搭话的间隔（秒）
idle_context_interval_seconds = 30.0

# 主动搭话时屏幕兴趣度下限
context_idle_min_score = 70.0

# 回复生成时注入的屏幕/网页缓存最大字符数
screen_context_max_chars = 1200
page_context_max_chars = 2500
```

### 多角色调度器

```toml
[multi_dialogue]
planning_model = ""
max_delay_seconds = 120.0
max_auto_followups = 1      # 用户每句话后允许的角色自动续接次数
log_decisions = true
screen_context_max_chars = 1200
page_context_max_chars = 2500
context_idle_min_score = 70.0
```

### TTS 语音合成

```toml
tts_backend = "minimax"     # minimax / cartesia
tts_volume = 1.0            # 0.0 ~ 2.0

# 流式刷新参数
tts_stream_chunk_chars = 28 # 累积多少字符强制刷新 TTS
tts_stream_sentence_min_chars = 8  # 触发句子刷新的最小字符数

# MiniMax 专用
minimax_api_key = ""
minimax_model = "speech-2.8-turbo"
minimax_sample_rate = 32000
minimax_tts_speed = 1.1
minimax_tts_buffer_seconds = 0.3   # 预缓冲秒数，越大越流畅但首音延迟越大
```

### STT 语音识别

```toml
[stt]
enabled = false
stt_model_dir = "models/stt"

# STT 精炼模型（修正同音错字）
stt_refine_model = "qwen2.5:1.5b"

# 精炼模式: separate / inline / none
stt_refine_mode = "inline"

# 端点检测静默时长（秒）
stt_refine_stable_seconds = 0.7

# 重叠分类模型（用户插话时判断打断等级）
overlap_model = "qwen2.5:0.5b"

# TTS 播放期间暂停麦克风（AEC 启用时无效）
stt_pause_during_tts = false

# AEC 漏回声时的文本兜底过滤窗口（秒）
stt_echo_filter_seconds = 45.0

# 回声文本过滤最短字符数
stt_echo_filter_min_chars = 3

# 回声文本过滤相似度阈值，越低越激进
stt_echo_filter_similarity = 0.68
```

`[stt].enabled = false` 表示主动闭麦：CLI 不加载 STT 模型、不打开麦克风，也不会把环境噪音当成用户输入。QQ、内在叙事流、主动搜索和其他输入仍会继续运行。

### QQ 自主参与

```toml
[qq]
packet_max_lines = 40
packet_max_age_seconds = 180.0
idle_packet_max_age_seconds = 90.0
batch_quiet_seconds = 1.0
idle_participation_seconds = 30.0
autonomous_participation_enabled = true
absorb_before_decide = false
participation_cooldown_seconds = 8.0
max_message_chars = 260

[qq.image_understanding]
enabled = true
auto_save_stickers = true
save_screenshots = false
save_photos = false
sticker_dir = "data/stickers"
```

`absorb_before_decide = false` 时，QQ 消息会先注册到输入事件，再由 QQ 参与判断读取当前 inner stream 和最新环境包快速决定是否回应；内在叙事流随后异步吸收。主动搜索可以继续并行触发，搜索结果回来后再作为输入影响后续内在叙事流。图片和表情包仍会被识别并反馈进内在叙事流，但截图/照片默认不会进入表情包库。

表情包库会保存语义档案，包括画面描述、图中文字、情绪、适用场景、风格、强度和使用说明。QQ 发送表情包前会用当前群聊和内在叙事流做本地关键词初筛，再把较多相关候选交给 LLM 选择。

### 内在认知反思

```toml
[inner_cognition]
enabled = true
consider_interval_seconds = 45.0
min_events = 1
max_event_chars = 5000
```

`inner_cognition` 会在内在叙事流更新后，把 QQ 群友、关系、项目和长期对象的稳定印象写入 `characters/{id}/cognition.json`。它和 `inner_memory` 不同：memory 记录发生过的一整件事，cognition 记录之后会反复影响态度和理解的稳定认识。

### 回声消除 (AEC)

```toml
[aec]
enabled = true
delay_ms = 50      # 麦克风-扬声器延迟估计
ns_level = 2       # 噪声抑制等级 0-4
auto_reset_on_tts_done = true
```

### 记忆后端

```toml
memory_backend = "mem0"   # none / mem0 / kokoromemo

[mem0.llm]
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "qwen2.5:1.5b"

[mem0.embedder]
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "bge-m3:latest"
embedding_dims = 1024

[mem0.lifecycle]
importance_mode = "auto"       # auto / always
search_threshold = 0.2
search_top_k = 12
max_memories_per_user = 200
compress_interval = 50
```

记忆数据存储在 `mem0_data/`，不同 embedding 模型使用不同子目录。

### 记忆事件系统

```toml
[memory_events]
enabled = true
eval_interval = 2       # 每 N 轮进行一次事件提取
eval_model = ""          # 留空复用 llm_model
```

### 屏幕监控

```toml
[screen_watch]
enabled = false
watch_interval = 3.0         # 截图分析最小间隔（秒）
interest_threshold = 70.0    # 兴趣度下限 0-100
vision_timeout = 45
```

### Edge 网页缓存

```toml
[edge_page_cache]
enabled = true
interval_seconds = 1.0
devtools_host = "127.0.0.1"
devtools_port = 9222
cache_file = "data/edge_page_cache.json"
max_chars = 12000
```

使用前需用调试端口启动 Edge：

```
msedge.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\alice-edge-debug"
```

### 立绘覆盖层

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0    # 0 = 使用后端默认
portrait_decay_seconds = 60.0       # 无语音后回退到平静表情的等待时间
portrait_model = "deepseek-v4-flash"
```

### 工具调用

```toml
[tool_calling]
enabled = true
tools = ["look_at_screen", "search_memory", "get_current_time", "get_current_app", "save_to_memory"]
max_iterations = 5
tool_timeout = 45.0
```

### Bilibili 直播

```toml
[bilibili_live]
enabled = false
room_id = 0
buffer_max_age = 60.0
```

### 云 API 密钥（放 config.json）

```json
{
  "deepseek_api_key": "sk-...",
  "minimax_api_key": "...",
  "charglm_api_key": "...",
  "vision_api_key": "..."
}
```

密钥来源优先级：环境变量 > `config.json` > 空的 `config.toml` 占位。
