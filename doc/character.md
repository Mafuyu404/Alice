# 角色系统

角色系统负责加载角色设定、生成 system prompt，并为 CLI/WebUI 提供可选角色列表。

## 文件布局

当前运行时从 `characters/` 目录扫描角色：

```text
characters/{character_id}/{character_id}.json
characters/{character_id}/portrait/portrait.json
characters/{character_id}/portrait/*.png
```

示例：

```text
characters/penglai/penglai.json
characters/penglai/portrait/portrait.json
characters/penglai/portrait/penglai_seated_hands_lap_quiet_neutral_p01.png
```

目录名就是角色 ID。角色 JSON 文件名必须与目录名一致，否则 `kokoro.character.load()` 不会加载它。

根目录的 `characters.json` 属于旧版聚合结构，当前主流程不再以它作为角色来源。

## 角色 JSON

最小结构：

```json
{
  "name": "蓬莱",
  "description": "精致小巧的人偶少女。",
  "personality": "高傲、嘴硬、直率。",
  "background": "爱丽丝制作的人偶。",
  "relationship": "和玩家是熟人。",
  "greeting": "你在干什么？",
  "example_dialogue": "玩家：你好。\n蓬莱：现在才想起来打招呼？",
  "proactive_guidance": "主动搭话时保持简短。",
  "tts_voice_id": "English_PlayfulGirl"
}
```

字段说明：

| 字段 | 用途 | 必需 |
| --- | --- | --- |
| `name` | 显示名，也用于对话中的角色名 | 是 |
| `description` | 外貌和总体气质 | 否 |
| `personality` | 说话方式、性格底色、行为边界 | 否 |
| `background` | 背景设定 | 否 |
| `relationship` | 与用户的关系 | 否 |
| `greeting` | CLI 启动后显示的问候语 | 否 |
| `example_dialogue` | 对话示例，帮助 LLM 校准语气 | 否 |
| `proactive_guidance` | 主动搭话额外约束 | 否 |
| `tts_voice_id` | 当前角色的 TTS 声线覆盖 | 否 |
| `system_prompt_template` | 覆盖全局角色 system prompt 模板 | 否 |
| `expression_calibration` | 覆盖全局表达校准规则 | 否 |

## System Prompt 构建

`kokoro.character.build_system_prompt(char)` 的规则：

1. 读取角色的 `name`、`description`、`personality`、`background`、`relationship`、`example_dialogue`。
2. 如果角色定义了 `system_prompt_template`，优先使用角色自己的模板。
3. 否则使用 `prompts.json` 中的 `character_system.template`。
4. 再拼接表达校准规则：优先使用角色自己的 `expression_calibration`，否则使用 `character_system.expression_calibration`。

因此，通用说话规范应放在 `prompts.json`，单个角色的特殊口吻或边界应放在角色自己的 JSON。

## 加载和选择

CLI 默认角色是 `alice`：

```bash
python cli.py
python cli.py --character penglai
python cli.py -c yuki
```

如果指定角色不存在，CLI 会打印当前可用角色列表。

WebUI 的 `/api/characters` 也调用同一个加载逻辑，因此它看到的角色列表与 CLI 一致。

## 新增角色

1. 新建目录 `characters/{id}/`。
2. 创建 `characters/{id}/{id}.json`。
3. 如需立绘，新建 `characters/{id}/portrait/`。
4. 放入 PNG 立绘。
5. 创建 `characters/{id}/portrait/portrait.json`，格式见 [portrait.md](portrait.md)。
6. 用 `python cli.py --character {id}` 测试。

## 当前角色

当前仓库中可加载的角色：

| ID | 角色文件 | 立绘数量 |
| --- | --- | --- |
| `alice` | `characters/alice/alice.json` | 96 |
| `penglai` | `characters/penglai/penglai.json` | 7 |
| `yuki` | `characters/yuki/yuki.json` | 0 |

`yuki` 目前有角色设定和空的立绘说明文件，但没有 PNG 立绘。
