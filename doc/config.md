# 配置说明

配置由两层组成：

- `config.toml`：主配置，可以提交。
- `config.json`：本地密钥和私有覆盖，已加入 `.gitignore`。

合并规则：`config.toml` 优先；当 TOML 中某项为空、缺失、空数组或空字典时，由 `config.json` 填充。

## LLM

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"
deepseek_api_key = ""
charglm_api_key = ""
local_model = "Qwen/Qwen2.5-1.5B-Instruct"
```

模型名以 `deepseek` 开头时自动走 DeepSeek API。其他模型默认走 `llm_url` 的 OpenAI 兼容接口。

## TTS

```toml
tts_backend = "minimax"
tts_volume = 1.0
```

`tts_volume` 是播放音量倍率：

- `0` 静音
- `0.5` 一半音量
- `1.0` 原始音量
- `2.0` 最大倍率，可能削波失真

MiniMax：

```toml
minimax_api_key = ""
minimax_model = "speech-2.8-turbo"
minimax_sample_rate = 32000
minimax_tts_speed = 1.1
minimax_tts_buffer_seconds = 0.3
```

Cartesia：

```toml
cartesia_api_key = ""
tts_voice_id = ""
tts_sample_rate = 24000
```

## STT

```toml
stt_model_dir = "models/stt"
stt_refine_model = "qwen2.5:1.5b"
stt_refine_mode = "inline"
stt_refine_stable_seconds = 0.7
stt_pool_tick_seconds = 0.05
stt_refine_max_tokens = 128
stt_skip_short_refine = true
stt_skip_short_refine_max_chars = 18
stt_pause_during_tts = true
```

`stt_refine_mode`：

- `separate`：独立 LLM 精炼，质量较高但延迟更高。
- `inline`：本地清洗 + 聊天 LLM 隐式纠错，延迟低。
- `none`：只做本地清洗。

## 人格层

```toml
cognition_model = ""
cognition_eval_interval = 5
emotion_model = ""
```

- `cognition_model` 为空时使用 `llm_model`。
- `emotion_model` 为空时使用 `stt_refine_model`。
- `cognition_eval_interval = 0` 可关闭周期性 cognition 评估。

## 记忆

```toml
memory_backend = "mem0"
kokoromo_url = "http://127.0.0.1:14514"
kokoromo_dir = "D:/program/kokoromemo"
```

可选后端：

- `none`
- `mem0`
- `kokoromemo`

mem0 的 LLM、embedding、生命周期参数在 `[mem0.*]` 段配置。

## 立绘和字幕

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0
portrait_decay_seconds = 60.0
portrait_click_through = false

[subtitle]
font_color = "#ffffff"
font_size = 24
subtitle_host = "127.0.0.1"
subtitle_port = 17353
```

`cli.py --no-portrait` 会同时关闭立绘相关启动；字幕客户端也不会启动。

## 视觉和屏幕监控

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

`screen_watch` 会周期性更新屏幕感知缓存。`impulse` 规划读取该缓存，不会额外触发截图。

## Edge 页面缓存

```toml
[edge_page_cache]
enabled = false
interval_seconds = 15.0
devtools_host = "127.0.0.1"
devtools_port = 9222
cache_file = "data/edge_page_cache.json"
max_chars = 12000
request_timeout = 3.0
```

开启后，后台线程通过 Edge DevTools Protocol 读取当前标签页正文，覆盖写入同一个 JSON 文件。`impulse` 规划会读取这个缓存。

## 主动搭话

```toml
[impulse]
enabled = true
max_plans = 5
min_plans = 1
planning_model = "deepseek-v4-flash"
screen_timeout = 45
empty_plan_retry_seconds = 10.0
log_plan_table = false
```

当前实现是计划表式 planner：每次空闲规划会增删改计划表，执行后重新规划。

## Bilibili 直播

```toml
[bilibili_live]
enabled = false
live_mode = true
room_id = 0
buffer_max_age = 60.0
reconnect_delay = 5.0
```

开启后，直播弹幕进入缓冲区。`impulse` 在空闲时根据弹幕上下文选择是否回复。

## 工具调用

```toml
[tool_calling]
enabled = true
tools = ["look_at_screen", "search_memory", "get_current_time", "get_current_app", "save_to_memory"]
max_iterations = 5
tool_timeout = 45.0
```

完整 CLI 的工具包括屏幕、记忆、时间、前台窗口和保存记忆。`text_cli.py` 使用独立的项目内文件工具，不读取此工具列表。

## 本地密钥示例

```json
{
  "deepseek_api_key": "sk-...",
  "minimax_api_key": "...",
  "cartesia_api_key": "...",
  "vision_api_key": "...",
  "tts_voice_id": "..."
}
```
