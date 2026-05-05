# 角色系统

## 角色定义文件

角色数据存储在 `characters.json` 中，每个角色一个条目：

```json
{
  "alice": {
    "name": "爱丽丝·玛格特罗伊德",
    "description": "金色长发，气质知性优雅……",
    "personality": "知性冷静，思考周密……",
    "background": "头脑聪明，博览群书……",
    "relationship": "和真冬住在一起，是关系很好的朋友……",
    "greeting": "哟，在忙什么呢？",
    "example_dialogue": "玩家：……\n爱丽丝：……",
    "proactive_guidance": "不要硬把屏幕内容和幻想设定扯上关系……"
  }
}
```

### 字段说明

| 字段 | 用途 | 必需 |
|------|------|------|
| `name` | 角色名称 | 是 |
| `description` | 外貌与气质描述（一句话概括） | 是 |
| `personality` | 性格描述（影响说话语气、用词风格、行为模式） | 是 |
| `background` | 背景设定（建议保持抽象，避免绑定具体物理场景） | 是 |
| `relationship` | 与用户的关系描述（含称呼方式、相处模式） | 否 |
| `greeting` | 初始问候语（启动时显示） | 否 |
| `example_dialogue` | 对话示例（帮助 LLM 理解语气语调），格式为 `玩家：...\n角色名：...` | 否 |
| `proactive_guidance` | 主动搭话时的额外行为指导（可空）。用于约束主动搭话的内容范围，例如避免强行关联幻想设定与屏幕内容 | 否 |

## 系统提示词构建

`kokoro/character.py` 中的 `build_system_prompt(char)` 函数将角色数据渲染到 `prompts.json` 的两个部分：

1. **`character_system.template`** — 角色设定 + 对话守则 + 格式要求。参数：`{name}` `{description}` `{personality}` `{background}` `{relationship}` `{background_block}` `{relationship_block}` `{example_dialogue_block}`。其中 `*_block` 参数在对应字段非空时分别渲染为 `【背景】...` `【关系】...` `【对话示例】...` 的带标题块
2. **`character_system.expression_calibration`** — 表达校准规则。拼接在 template 之后，约束语音对话的写实性、回答风格（如：不说"正在输入"、不描述动作、不主动问"还有什么需要"等）

两者以 `\n\n` 拼接后作为最终的 system prompt。

## 角色 CRUD

`kokoro/character.py` 提供角色的读写接口：

| 函数 | 功能 |
|------|------|
| `load()` | 从 `characters.json` 加载所有角色，返回 `dict[str, dict]` |
| `save(characters)` | 将角色字典写回 `characters.json` |
| `get_display(char)` | 返回格式化显示字符串：`名称 - 描述前40字` |

WebUI 提供完整的角色 CRUD REST API（见 [webui.md](webui.md)），也可直接编辑 `characters.json`。

## 场景注入

`cli.py` 在构建对话时，根据场景在消息列表中额外注入：

- **主动搭话触发时**：插入 `proactive.trigger_system` 提示词 + 角色的 `proactive_guidance`（通过 `proactive.trigger_guidance_label` 模板格式化）+ 对应行为提示词（`proactive.idle` 等）
- **屏幕内容触发时**：插入屏幕分析结果上下文 + `proactive.screen_context_label`
- **记忆事件触发时**：插入记忆上下文 + `proactive.mem_context_label`
- **屏幕历史记录**：自动在 `build_messages()` 中注入最近 3 条屏幕观察记录（使用 `chat_session.screen_context_prefix` 格式化）

## 启动指定

启动时通过 `--character alice` 参数指定角色 ID，默认为 `alice`。WebUI 可通过界面切换。
