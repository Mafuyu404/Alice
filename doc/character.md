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
    "relationship": "和玩家是关系很好的朋友",
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
| `description` | 一句话简介 | 是 |
| `personality` | 性格描述（影响说话语气和用词） | 是 |
| `background` | 背景设定（建议保持抽象，避免绑定物理场景） | 是 |
| `relationship` | 与用户的关系称呼 | 是 |
| `greeting` | 初始问候语 | 是 |
| `example_dialogue` | 对话示例（帮助 LLM 理解语气语调） | 否 |
| `proactive_guidance` | 主动搭话时的额外指导（可空） | 否 |

## 系统提示词构建

`kokoro/character.py` 中的 `build_system_prompt()` 函数将角色数据渲染到 `prompts.json` 的两个部分：

1. **`character_system.template`** — 角色设定 + 对话守则 + 格式要求
2. **`character_system.expression_calibration`** — 表达校准规则（语音对话约束、回答风格等）

两者拼接后作为最终的 system prompt。

### 场景注入

`cli.py` 在构建对话时，根据场景在消息列表中额外注入：

- **主动搭话触发时**：插入 `proactive.trigger_system` 提示词 + 角色的 `proactive_guidance`（通过 `proactive.trigger_guidance_label` 模板格式化）
- **屏幕内容触发时**：插入屏幕分析结果上下文
- **记忆事件触发时**：插入记忆上下文描述
- **屏幕历史记录**：自动在 `build_messages()` 中注入最近 3 条屏幕观察记录

## 角色管理

WebUI 提供角色 CRUD 接口（见 [webui.md](webui.md)），可直接在浏览器中编辑角色，也可直接编辑 `characters.json`。

启动时通过 `--character alice` 参数指定角色 ID，默认为 `alice`。
