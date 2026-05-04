# 角色系统

## 角色定义文件

角色数据存储在 `characters.json` 中，每个角色一个条目：

```json
{
  "alice": {
    "name": "爱丽丝",
    "description": "雾雨魔法店的魔法使",
    "personality": "……",
    "background": "……",
    "relationship": "你",
    "greeting": "……",
    "proactive_guidance": "主动向用户搭话时的特殊指导。不要生硬地……"
  }
}
```

### 字段说明

| 字段 | 用途 | 必需 |
|------|------|------|
| `name` | 角色名称 | 是 |
| `description` | 一句话简介 | 是 |
| `personality` | 性格描述 | 是 |
| `background` | 背景故事 | 是 |
| `relationship` | 与用户的关系称呼（如"你""主人""朋友"） | 是 |
| `greeting` | 初始问候语 | 是 |
| `proactive_guidance` | 主动搭话时的额外指导（可空） | 否 |

## 系统提示词构建

`kokoro/character.py` 中的 `build_system_prompt()` 函数将角色数据渲染到 `prompts.json` 的 `character_system.template` 模板中。

### 模板格式

```
\\n## 角色设定\\n名称: {name}\\n简介: {description}\\n性格: {personality}\\n背景: {background}\\n称呼: {relationship}
```

### 场景注入

`cli.py` 和 `webui.py` 在构建对话时，根据场景在系统提示词后追加额外指令：

- **主动搭话触发时**：追加 `proactive.trigger_system` 提示词 + 角色的 `proactive_guidance`
- **屏幕内容触发时**：追加屏幕分析结果描述 + 禁止编造指令
- **记忆事件触发时**：追加记忆上下文描述

## 角色管理

WebUI 提供角色 CRUD 接口（见 [webui.md](webui.md)），可直接在浏览器中编辑角色。也可直接编辑 `characters.json`。

启动时通过 `--character alice` 参数指定角色 ID，默认为 `alice`。
