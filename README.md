# Alice Chat Framework

AI role-play chat framework with two maintained entrypoints:

- `webui.py`: text-only Web UI.
- `cli.py`: voice CLI with STT input and optional TTS output.

The shared chat logic lives under `kokoro/`. Runtime configuration is in `config.toml`, character data is in `characters.json`, and all maintainable prompt templates are in `prompts.json`.

## Current Architecture

```text
webui.py
  Browser text input
  -> FastAPI /v1/chat/completions
  -> kokoro.llm_client
  -> LLM provider
  -> optional memory injection through kokoro.memory

cli.py
  Microphone
  -> kokoro.stt
  -> kokoro.pool STT refinement
  -> kokoro.chat_session
  -> kokoro.llm_client
  -> optional kokoro.tts playback
  -> optional memory storage through kokoro.memory
  -> optional proactive speech scheduling through kokoro.proactive
```

`chat.py` is intentionally deprecated and only prints a migration notice. Use `webui.py` for text chat.

## Project Layout

```text
.
鈹溾攢鈹 cli.py                       # Voice/STT CLI entrypoint
鈹溾攢鈹 webui.py                     # Text-only Web UI entrypoint
鈹溾攢鈹 chat.py                      # Deprecated compatibility shim
鈹溾攢鈹 index.html                   # Web UI frontend
鈹溾攢鈹 config.toml                  # Runtime configuration
鈹溾攢鈹 prompts.json                 # Prompt templates
鈹溾攢鈹 portrait_notes.json          # Compact portrait ids + notes for LLM selection
鈹溾攢鈹 characters.json              # Character definitions
鈹溾攢鈹 local_llm.py                 # Optional local transformers-compatible LLM server
鈹溾攢鈹 overlay_slideshow.py         # Optional transparent portrait overlay service
鈹溾攢鈹 run_portrait_overlay.bat     # Windows helper for portrait overlay
鈹溾攢鈹 img/portrait_map.json        # Portrait metadata
鈹溾攢鈹 models/                      # STT model cache
鈹溾攢鈹 mem0_data/                   # Local mem0/Qdrant data
鈹斺攢鈹 kokoro/
    鈹溾攢鈹 config.py                # config.toml accessors
    鈹溾攢鈹 prompts.py               # prompts.json loader and formatter
    鈹溾攢鈹 character.py             # character storage and system prompt generation
    鈹溾攢鈹 chat_session.py          # shared character session, history, memory injection
    鈹溾攢鈹 llm_client.py            # shared LLM routing, payloads, SSE parsing
    鈹溾攢鈹 memory.py                # none / mem0 / kokoromemo memory backends
    鈹溾攢鈹 pool.py                  # STT text accumulation and refinement
    鈹溾攢鈹 proactive.py             # proactive speech desire/disturbance scheduler
    鈹溾攢鈹 stt.py                   # sherpa-onnx STT helpers
    鈹溾攢鈹 tts.py                   # TTS backend dispatcher
    鈹溾攢鈹 tts_cartesia.py          # Cartesia TTS backend
    鈹溾攢鈹 tts_minimax.py           # MiniMax TTS backend
    鈹斺攢鈹 vision.py                # Screen capture + vision recognition (DashScope / Ollama)
```

## Quick Start

### 1. Install Dependencies

Install only what you need for your mode.

```powershell
# Core
pip install requests numpy

# Web UI
pip install fastapi uvicorn httpx pydantic

# CLI STT
pip install sherpa-onnx sounddevice

# Cartesia TTS
pip install "cartesia[websockets]" soundfile

# MiniMax TTS
pip install websockets soundfile

# Local mem0 memory backend
pip install mem0ai fastembed
```

### 2. Prepare LLM Backend

For local Ollama models:

```powershell
ollama pull qwen2.5:1.5b
ollama list
```

The project can also route `deepseek-*` model names to the configured DeepSeek API key.

### 3. Configure `config.toml`

Minimal local text chat:

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "qwen2.5:1.5b"
available_models = ["qwen2.5:1.5b"]
memory_backend = "none"
```

Voice CLI with MiniMax TTS:

```toml
tts_backend = "minimax"
minimax_api_key = "your-minimax-key"
minimax_model = "speech-2.8-turbo"
minimax_sample_rate = 32000
llm_url = "http://127.0.0.1:11434"
llm_model = "qwen2.5:1.5b"
stt_refine_model = "qwen2.5:1.5b"
stt_pause_during_tts = true
memory_backend = "none"
```

Voice CLI with Cartesia TTS:

```toml
tts_backend = "cartesia"
cartesia_api_key = "your-cartesia-key"
tts_voice_id = "your-cartesia-voice-id"
tts_sample_rate = 24000
```

Do not commit real API keys.

## Entrypoints

### Text Web UI

```powershell
python webui.py
```

Open:

```text
http://127.0.0.1:8080
```

Web UI supports text chat only. It does not expose server-side TTS endpoints.

Available HTTP routes:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Web chat page |
| `GET` | `/api/characters` | List characters |
| `GET` | `/api/characters/{key}` | Get one character |
| `POST` | `/api/characters/{key}` | Create character |
| `PUT` | `/api/characters/{key}` | Update character |
| `DELETE` | `/api/characters/{key}` | Delete character |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/models` | Model list |
| `POST` | `/api/models/switch` | Switch active model |
| `POST` | `/v1/chat/completions` | Streaming chat completions |

### Voice CLI

```powershell
python cli.py
```

Common options:

```powershell
python cli.py --character alice
python cli.py --model qwen2.5:1.5b
python cli.py --list-devices
python cli.py --device 1
python cli.py --no-tts
python cli.py --no-portrait
python cli.py --no-proactive
python cli.py --no-screen-watch
```

CLI flow:

1. Capture microphone audio through `sounddevice`.
2. Decode speech through `kokoro.stt`.
3. Accumulate and refine stable STT text through `kokoro.pool`.
4. Build character chat context through `kokoro.chat_session`.
5. Stream LLM output through `kokoro.llm_client`.
6. Push reply text to `kokoro.tts` if TTS is enabled.
7. Store useful conversation memory if memory is enabled.
8. Submit the latest user/reply pair to the portrait decision worker if portrait overlay is enabled.
9. Optionally run `kokoro.proactive` in the background to decide whether Alice may start a brief active-speech turn.

## Proactive Speech Scheduler

`kokoro/proactive.py` implements the first stable slice of `alice.md`'s active-speech design:

- IDLE desire slowly accumulates while the user is quiet, with extra gain after idle time and recent conversation.
- RECENT desire is injected when a conversation ends, waits briefly, then decays so follow-up remarks remain time-sensitive.
- SCREEN desire can be fed by the optional screen watcher, which uses `kokoro.screen_interest` and `kokoro.vision` to score safe, comment-worthy screen content.
- MEM desire can be fed by optional date rules and low-frequency memory lookup through `kokoro.memory_events`.
- Each behavior is filtered by a disturbance score, per-behavior disturbance limits, cooldown, and recent-behavior diversity.

Enable it in `config.toml`:

```toml
[proactive]
enabled = true
tick_seconds = 20.0
active_threshold = 70.0 # range 60-85: lower means more talkative
cooldown_seconds = 90.0
recent_decay_delay_seconds = 30.0
screen_watch_enabled = false
screen_watch_interval = 45.0
screen_interest_threshold = 70.0
screen_vision_timeout = 45
memory_events_enabled = false
memory_check_interval = 300.0
memory_cooldown_seconds = 21600.0
memory_date_score = 50.0
memory_lookup_score = 70.0
memory_lookup_query = "recent important user preferences, plans, dates, anniversaries, goals"

[[proactive.memory_date_events]]
date = "05-04"
label = "Alice project anniversary"
note = "Mention it only if the user seems idle and the mood is relaxed."
```

The CLI also accepts `--no-proactive` and `--no-screen-watch` to force features off for a run. User speech resets all proactive desires, so direct user input always interrupts active-speech scheduling.

Screen watching is disabled by default because it captures screenshots and may call a vision model. Before each vision call, the screen watcher checks the foreground window title/process for privacy-sensitive contexts such as passwords, payment, banking, private browsing, and meetings. If the guard trips, it suppresses SCREEN desire and temporarily raises quiet time.

Memory events are also disabled by default. Date rules support `YYYY-MM-DD` for one-off dates and `MM-DD` for yearly dates. Memory lookup only runs if the selected memory backend reports `ready`; it injects a tentative context into MEM desire and relies on MEM's stricter disturbance limit, cooldown, and active threshold before Alice speaks.

## Prompt Maintenance

All maintainable prompts are in root-level `prompts.json`.

```json
{
  "character_system": {
    "template": "..."
  },
  "stt_refine": {
    "system": "...",
    "user_template": "..."
  },
  "memory_importance": {
    "user_template": "..."
  }
}
```

Template variables currently used:

| Prompt | Variables |
|---|---|
| `character_system.template` | `name`, `description`, `personality`, `background`, `relationship`, `background_block`, `relationship_block`, `example_dialogue` |
| `stt_refine.user_template` | `text` |
| `memory_importance.user_template` | `user_msg`, `assistant_msg` |
| `portrait_selection.user_template` | `current_id`, `user_text`, `assistant_text`, `catalog` |

`kokoro/prompts.py` loads `prompts.json` and provides defaults if the file is missing or incomplete.

## Character Data

Characters are stored in `characters.json`.

```json
{
  "alice": {
    "name": "Alice",
    "description": "One-line setting",
    "personality": "Personality description",
    "background": "Background story",
    "relationship": "Relationship to the user",
    "greeting": "Opening greeting",
    "example_dialogue": "Optional examples"
  }
}
```

`kokoro.character.build_system_prompt()` combines character fields with `prompts.json`'s `character_system.template`.

## LLM Routing

Shared LLM logic is in `kokoro/llm_client.py`.

- `deepseek-*` model names are sent to `https://api.deepseek.com/v1`.
- Other models are sent to `cfg.api_base()` for CLI or the selected upstream for Web UI.
- Streaming SSE delta parsing is centralized in `parse_sse_delta()`.

Important config keys:

| Key | Meaning |
|---|---|
| `llm_url` | Local OpenAI-compatible/Ollama base, usually `http://127.0.0.1:11434` |
| `llm_model` | Default chat model |
| `available_models` | Models shown in Web UI switcher |
| `deepseek_api_key` | DeepSeek API key for `deepseek-*` models |
| `kokoromo_url` | Optional KokoroMemo memory proxy URL |

## Memory Backends

Configured by `memory_backend`.

| Value | Behavior |
|---|---|
| `none` | No memory injection or storage |
| `mem0` | Local mem0 + Qdrant storage in `mem0_data/` |
| `kokoromemo` | Delegate memory management to KokoroMemo service |

Example mem0 config:

```toml
memory_backend = "mem0"

[mem0.llm]
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "qwen2.5:1.5b"

[mem0.embedder]
provider = "fastembed"
model = "BAAI/bge-small-zh-v1.5"
embedding_dims = 512

[mem0.lifecycle]
importance_mode = "auto"
search_threshold = 0.3
search_top_k = 8
```

Memory importance judging uses `prompts.json` key `memory_importance.user_template`.

## TTS Backends

Configured by `tts_backend`.

| Value | Module | Notes |
|---|---|---|
| `minimax` | `kokoro.tts_minimax` | Uses MiniMax T2A WebSocket API |
| `cartesia` | `kokoro.tts_cartesia` | Uses Cartesia Sonic backend |

The dispatcher is `kokoro/tts.py`; callers import `from kokoro import tts`.

CLI initializes `StreamingTTS()` unless `--no-tts` is passed. Web UI does not use TTS.

MiniMax note: the streaming implementation treats `is_final=true` as the end of the current text segment and has timeout protection to avoid blocking the next STT turn.

## STT

STT is CLI-only.

Useful commands:

```powershell
python cli.py --list-devices
python cli.py --device 1
```

Relevant config:

| Key | Meaning |
|---|---|
| `stt_model_dir` | STT model cache directory |
| `stt_refine_model` | Model used by `kokoro.pool` to refine raw STT text |
| `stt_pause_during_tts` | If true, microphone frames are skipped while TTS is playing |

`kokoro.pool` uses `prompts.json` key `stt_refine.*`.

## Vision Recognition

Standalone module: `kokoro/vision.py` — full-screen capture + vision LLM recognition.

Supported backends:

| Backend | Model | Requirement |
|---|---|---|
| `dashscope` (default) | `qwen-vl-plus` | `vision_api_key` set in config or `DASHSCOPE_API_KEY` env |
| `ollama` | `qwen2.5vl:3b` | Local Ollama with the model pulled |

CLI usage:

```powershell
# Default: screenshot + running-window info combined
python -m kokoro.vision

# Custom prompt
python -m kokoro.vision -p "屏幕上有什么文字？"

# Screenshot only (skip app enumeration)
python -m kokoro.vision --no-apps

# Local Ollama
python -m kokoro.vision --backend ollama

# Override model
python -m kokoro.vision --model qwen-vl-max

# List running windows only (no vision call)
python -m kokoro.vision --apps
```

In code:

```python
from kokoro import vision

# Basic screenshot recognition
text = vision.describe("描述这张截图")

# Desktop-aware: screenshot + running apps + foreground window
text = vision.detect_desktop("总结当前桌面状态")

# Window enumeration only
apps = vision.get_running_apps()
fg = vision.get_foreground_app()
print(vision.format_apps(apps, fg))
```

Relevant config:

```toml
vision_backend = "dashscope"          # "dashscope" or "ollama"
vision_model = "qwen-vl-plus"          # model name override
vision_api_key = "sk-..."              # DashScope API key (or DASHSCOPE_API_KEY env)
```

`kokoro/screen_interest.py` wraps vision recognition for proactive use. It asks the vision model for compact JSON containing `score`, `summary`, `reason`, and `private`; only safe results above `screen_interest_threshold` are injected into the proactive SCREEN desire.

## Memory Events

`kokoro/memory_events.py` turns special dates and long-term memory search into proactive MEM events. It does not write new memories; it only asks the configured memory backend for context at a low frequency and applies event-level cooldown so the same reminder does not repeat throughout the day.

## Optional Portrait Overlay

`overlay_slideshow.py` is a transparent portrait overlay service. `cli.py` starts it automatically by default and controls it through HTTP. Use `--no-portrait` to disable that behavior.

The CLI portrait selector runs as one continuous worker thread. It repeatedly reads the latest dialogue state, reads the current overlay status, asks the LLM for the best portrait, applies changes, then immediately loops again after `portrait_decision_interval` seconds. It is not only triggered when a new dialogue arrives.

The LLM does not read the full `img/portrait_map.json`. A compact root-level `portrait_notes.json` contains only:

```json
{
  "portraits": [
    {
      "id": "main_host_capable_v1_book_down_calm_p02.png",
      "notes": "short visual note"
    }
  ]
}
```

Regenerate it from `img/portrait_map.json` if portrait metadata changes:

```powershell
python -c "import json; from pathlib import Path; data=json.loads(Path('img/portrait_map.json').read_text(encoding='utf-8')); out={'version':1,'source':'img/portrait_map.json','portraits':[{'id':a.get('new_name',''),'notes':a.get('notes','')} for a in data.get('assets', []) if a.get('new_name')]}; Path('portrait_notes.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')"
```

Manual start:

Start:

```powershell
python overlay_slideshow.py
```

On Windows, if Tk initialization is unstable:

```powershell
run_portrait_overlay.bat
```

Default server:

```text
http://127.0.0.1:17352
```

Useful routes:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/status` | Current overlay status |
| `GET` | `/portraits` | List/filter portraits |
| `POST` | `/control` | Show/pause/play/click-through/shutdown controls |

Example:

```powershell
curl -X POST http://127.0.0.1:17352/control `
  -H "Content-Type: application/json" `
  -d "{\"action\":\"show\",\"series\":\"main_host_capable_v2\",\"emotion\":\"happy\",\"random\":true}"
```

Overlay state is persisted in `portrait_overlay_state.json`.

Optional config:

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 2.0
portrait_click_through = false
```

## Development Checks

Syntax check:

```powershell
python -m py_compile cli.py webui.py chat.py kokoro\character.py kokoro\pool.py kokoro\memory.py kokoro\memory_events.py kokoro\prompts.py kokoro\chat_session.py kokoro\llm_client.py kokoro\proactive.py kokoro\screen_interest.py kokoro\vision.py
```

Prompt JSON check:

```powershell
python -m json.tool prompts.json
```

Import smoke test:

```powershell
python -c "import cli, webui; import kokoro.llm_client, kokoro.chat_session, kokoro.prompts; print('ok')"
```

List characters:

```powershell
python -c "from kokoro import character; print(list(character.load().keys()))"
```

List TTS voices:

```powershell
python -c "from kokoro import tts; print(tts.get_voices())"
```

## Operational Notes

- Keep real API keys out of commits.
- `config.toml` selects runtime behavior; `prompts.json` controls prompt wording.
- If mem0 reports a Qdrant lock error, stop other running instances that may be using `mem0_data/`.
- Web UI is text-only by design. Use CLI for STT and TTS.
- `chat.py` is deprecated; do not add new features there.
