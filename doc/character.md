# 角色系统

## 角色目录结构

```text
characters/
  alice/                     # 角色 ID（目录名 = 加载时的 key）
    alice.json               # 角色主设定文件
    config.toml              # 角色级配置覆盖（可选）
    cognition.json           # 认知层持久化（自动生成）
    emotion.txt              # 情绪层持久化（自动生成）
    portrait/                # 立绘资源目录
      portrait.json          # 立绘配置
      1.png                  # 按 id 命名的立绘文件
      ...
  penglai/                   # 另一个角色，结构同上
    penglai.json
    ...
```

## 角色文件字段

`characters/{id}/{id}.json` 的结构：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 角色名，对话中以此名称呼 |
| `description` | 推荐 | 简短描述，约 1-2 句 |
| `personality` | 推荐 | 性格描述，影响说话语气和话轮倾向 |
| `background` | 推荐 | 背景故事，仅作为语气底色 |
| `scene` | 可选 | 场景参考，渲染环境感 |
| `example_dialogue` | 可选 | 对话示例，展示说话风格 |
| `expression_calibration` | 可选 | 覆盖全局的"说话节奏"校准 |
| `greeting` | 可选 | 启动时显示的第一句话 |
| `tts_voice_id` | 可选 | TTS 音色 ID |
| `system_prompt_template` | 可选 | 覆盖全局模板 |

## System Prompt 构建流程

入口：`character.build_system_prompt()`

```text
prompts/character_system.toml ?? character_system.template??????
  ├─ {name} → 角色名
  ├─ {user_name} → 用户称呼
  ├─ {description}
  ├─ {personality}
  ├─ {background} → 包装成【背景】块
  ├─ {scene} → 包装成【场景参考】
  ├─ {example_dialogue} → 包装成【对话示例】
  └─ {scene_block}

加上 expression_calibration（角色级或全局默认）
```

### 模板核心规则

当前默认模板强调以下原则：

1. **以对话为先**：回应的是对方说的话，不是自己的设定
2. **人设提供语气，不提供话题**：性格是底色，不是素材库
3. **说话方式**：短、直接、不写动作描写、不用括号
4. **不提及 AI 技术概念**：不说"作为 AI"、"提示词"等

## 角色级配置覆盖

`characters/{id}/config.toml` 可覆盖全局配置。支持字段：

- `llm_model` — 该角色使用的模型
- `llm_url` — 该角色使用的 LLM 地址

应用场景：多角色对话中不同角色走不同模型（如 alice 用 deepseek，penglai 用本地模型）。

## Cognition / Emotion 持久化

- `cognition.json`：自动维护，存储角色对人物/关系/事物的稳定认知
- `emotion.txt`：自动维护，存储浅层情绪基调和中期动机
- 这些文件由运行时自动读写，一般不需要手动编辑

## 多角色场景要求

- 不同角色必须有独立的记忆范围（通过 user_id 隔离）
- 不同角色有独立的 cognition / emotion 实例
- 多角色调度器读取各自角色的运行时上下文做决策
