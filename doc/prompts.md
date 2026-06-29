# 提示词系统

## 结构

所有 LLM 提示词集中在项目根目录的 `prompts/` 下，不放在程序文件里。

```text
prompts/
  character_system.toml
  inner_stream.toml
  autonomous_step.toml
  dialogue_orchestrator.toml
  multi_dialogue_orchestrator.toml
  ...
  skills/
    inner_continuity.md
    memory_cognition.md
    social_presence.md
```

入口：

```python
from kokoro.core import prompts

prompts.get("dialogue_orchestrator.planner_system")
prompts.format_prompt("character_system.template", name="Alice", user_name="用户")
prompts.skill("inner_continuity")
```

加载器位于 `kokoro/core/prompts.py`。它会读取 `prompts/*.toml` 并合并为一个按点分路径访问的字典；技能类长文档从 `prompts/skills/*.md` 读取。

## TOML 约定

每个 TOML 文件按模块命名，并只放该模块的提示词：

```toml
# Prompt module: inner_stream
# Entry prefix: inner_stream.*

[inner_stream]
# Entry: inner_stream.events_system
events_system = '''
...
'''
```

约定：

- 文件名与顶层表名一致，例如 `dialogue_orchestrator.toml` 内使用 `[dialogue_orchestrator]`。
- key 保持稳定，程序只引用 dotted key，例如 `inner_stream.events_user`。
- 每个重要入口前写 `# Entry: module.key` 注释，说明调用入口。
- 多行提示词使用 TOML 的 `'''...'''`，避免转义干扰。
- 程序里只保留变量组装、格式化和调用逻辑；不要写 LLM 身份、输出格式、JSON 约束等提示词正文。

## 主要模块

| 模块 | 用途 |
|---|---|
| `character_system.*` | 角色主 system prompt 与说话节奏校准 |
| `inner_stream.*` | 内在叙事流更新 |
| `inner_memory_reflection.*` | 从内在叙事流判断是否沉淀记忆 |
| `autonomous_step.*` | 内在叙事流之后的行动批次选择 |
| `dialogue_orchestrator.*` | 一对一对话调度、STT 池判断、回复生成边界 |
| `multi_dialogue_orchestrator.*` | 多角色对话调度与生成边界 |
| `tool_calling.*` / `tool_handlers.*` | 工具能力说明与工具结果解释 |
| `vision.*` / `screen_interest.*` | 屏幕与图像理解 |
| `web_search_impulse.*` | 内在搜索冲动判断 |
| `deepseek_api.*` | DeepSeek 兼容调用的共享缓存前缀 |

## 设计原则

1. 提示词集中管理：新增 LLM 行为先加 `prompts/<module>.toml`，再在代码里引用 key。
2. 程序只做信息循环：收集上下文、调用 LLM、执行工具、回流结果。
3. 内在叙事流优先：对话、搜索、记忆、观察、主动说话都只是围绕内在叙事流展开的行动能力。
4. JSON 输出要求写在 TOML 中；代码只启用 `json_mode`、解析和兜底。
5. 技能类长提示放 `prompts/skills/*.md`，由需要的模块显式拼入 system prompt。
