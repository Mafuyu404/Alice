# WebUI

`webui.py` 提供浏览器文字聊天界面和一组 REST/SSE API。它适合调试角色、测试模型和进行纯文字对话。

WebUI 不启动以下 CLI 专属功能：

- STT 麦克风输入
- TTS 语音输出
- 立绘覆盖层
- 主动搭话
- 屏幕感知
- 自然语言屏幕命令

## 启动

```bash
python webui.py
```

默认监听：

```text
http://127.0.0.1:8080
```

启动时会读取：

- `config.toml`
- `config.json`
- 环境变量
- `characters/` 目录下的角色定义

如果 `LLM_BACKEND_CMD` 环境变量已设置，并且当前 `llm_url` 不可用，WebUI 会尝试启动该命令作为 LLM 后端。

当 `memory_backend = "kokoromemo"` 且 `kokoromo_dir` 指向有效安装目录时，WebUI 会尝试启动 `kokoromemo-server.exe`，退出时通过 `atexit` 清理子进程。

## API

### 系统

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/` | GET | 返回 `index.html` |
| `/api/health` | GET | 返回 LLM 和记忆后端状态 |

### 模型

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/models` | GET | 返回当前模型和 `available_models` |
| `/api/models/switch` | POST | 切换运行时模型 |

切换模型请求：

```json
{
  "model": "deepseek-v4-flash"
}
```

模型必须在 `config.toml` 的 `available_models` 中。

### 角色

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/characters` | GET | 扫描并返回 `characters/` 下所有角色 |
| `/api/characters/{key}` | GET | 返回单个角色 |
| `/api/characters/{key}` | POST | 创建角色 |
| `/api/characters/{key}` | PUT | 更新角色 |
| `/api/characters/{key}` | DELETE | 删除角色 |

读取角色时，WebUI 使用 `kokoro.character.load()`，也就是扫描：

```text
characters/{key}/{key}.json
```

注意：当前 `webui.py` 的写入端点调用了 `character.save(chars)`，但 `kokoro/character.py` 当前没有实现 `save()`。因此 GET 端点可用，角色创建/更新/删除端点需要先补齐保存逻辑，或直接编辑 `characters/{id}/{id}.json`。

WebUI 的 `CharacterModel` 当前包含字段：

```python
class CharacterModel(BaseModel):
    name: str
    description: str = ""
    personality: str = ""
    background: str = ""
    greeting: str = ""
    example_dialogue: str = ""
```

如果需要 `relationship`、`proactive_guidance`、`tts_voice_id`、`system_prompt_template` 或 `expression_calibration`，请直接编辑角色 JSON。

### 聊天

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/v1/chat/completions` | POST | OpenAI 兼容聊天接口，支持流式响应 |

请求示例：

```json
{
  "model": "deepseek-v4-flash",
  "character_id": "penglai",
  "stream": true,
  "messages": [
    {"role": "user", "content": "你好"}
  ]
}
```

行为：

- `model` 未提供时使用当前 WebUI 模型。
- `character_id` 会作为记忆 user id 使用。
- 当 `memory_backend = "mem0"` 且后端 ready，会把最近用户输入检索到的记忆注入 messages。
- 当 `memory_backend = "kokoromemo"`，会优先走 KokoroMemo 可用性检查和对应代理逻辑。
- 真实 LLM 请求由 `kokoro.llm_client` 构建并发送到 OpenAI 兼容接口。

## 与 CLI 的区别

| 能力 | WebUI | CLI |
| --- | --- | --- |
| 文字聊天 | 是 | 是 |
| OpenAI 兼容接口 | 是 | 否 |
| 模型运行时切换 | 是 | 启动参数指定 |
| 角色读取 | 是 | 是 |
| 角色写入 | 端点存在，但当前需补 `character.save()` | 直接编辑文件 |
| 记忆注入 | 是 | 是 |
| STT | 否 | 是 |
| TTS | 否 | 是 |
| 立绘覆盖层 | 否 | 是 |
| 主动搭话 | 否 | 是 |
| 屏幕感知 | 否 | 是 |

## 常用调试

检查健康状态：

```bash
curl http://127.0.0.1:8080/api/health
```

列出模型：

```bash
curl http://127.0.0.1:8080/api/models
```

列出角色：

```bash
curl http://127.0.0.1:8080/api/characters
```
