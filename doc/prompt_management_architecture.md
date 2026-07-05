# Prompt Management Architecture

本文档描述新的 `kokoro/prompt` 提示词管理架构。目标不是把现有 `prompts/` 换一个目录，而是把提示词从散落字符串升级成可注册、可渲染、可追踪、可调试、可分层的运行系统。

核心原则：

- 程序只是骨架，提示词工程是主体。
- LLM 自主决策优先，程序只负责装配、校验、预算、缓存、diff、日志。
- 全局层只写开放框架，不写具体角色关系。
- 角色关系只属于角色层。
- 工具规范只属于工具层。
- 运行事实只属于状态层。
- 调试模式只增加可见性和干预入口，不削弱功能。

## 设计目标

现有提示词的问题主要不是文件位置，而是边界不清：

- 全局提示词容易混入单一角色设定。
- 工具结果、屏幕观察、调试日志容易污染内在叙事。
- 每轮上下文容易全量塞入，导致慢、冗余、注意力漂移。
- 工具 prepare / after / action 规范散在代码和 prompt 中，不利于模块化。
- 模板变量缺失、字段多余、旧 prompt 残留时没有严格诊断。

新的 `kokoro/prompt` 要解决这些问题：

- 用 `PromptFragment` 表示每个提示词片段。
- 用 marker 明确区分提示词片段、运行事实和自然输入。
- 用 snapshot/diff 控制重复上下文。
- 用 strict renderer 阻止模板静默损坏。
- 用 diagnostics 输出完整 prompt trace。
- 用工具模块自带 prompt 的方式支持渐进披露。

## 目录结构

建议新增目录：

```text
kokoro/prompt/
  __init__.py
  manager.py
  fragment.py
  registry.py
  renderer.py
  context.py
  state.py
  budget.py
  diagnostics.py
  contracts.py

  templates/
    life/
      base.md
      inner_stream_tick.md
      context_compact.md
      patch_fallback.md
      json_repair.md

    dialogue/
      orchestrator.md
      multi_orchestrator.md
      overlap.md
      stt_refine.md

    memory/
      events.md
      cognition.md
      importance.md
      reflection.md

    vision/
      describe.md
      screen_interest.md
      user_command.md

    tools/
      tool_calling.md
      agent_guard.md

  fragments/
    global_life.py
    character.py
    life_runtime.py
    tool_catalog.py
    tool_runtime.py
    memory.py
    environment.py
    debug.py
```

职责：

- `manager.py`：对外统一入口，负责加载、渲染、组装、导出 trace。
- `fragment.py`：定义 `PromptFragment`、`RenderedFragment`、marker 协议。
- `registry.py`：注册所有片段，按场景选择片段。
- `renderer.py`：严格模板渲染。
- `context.py`：统一的 `PromptContext`，包含角色、时间、工具、记忆、调试等事实。
- `state.py`：片段 snapshot 和 diff。
- `budget.py`：字符/token 预算。
- `diagnostics.py`：prompt trace、缺变量、超预算、重复片段诊断。
- `contracts.py`：JSON 输出契约、工具调用契约等结构性约束。

## PromptFragment

所有提示词片段都必须是结构化对象，而不是随手拼接的字符串。

建议模型：

```python
from dataclasses import dataclass
from typing import Callable, Literal

@dataclass
class PromptFragment:
    id: str
    role: Literal["system", "developer", "user"]
    scope: Literal["global", "character", "runtime", "tool", "debug"]
    priority: int
    template_path: str | None = None
    marker: tuple[str, str] | None = None
    budget: int | None = None
    required_vars: set[str] | None = None
    condition: Callable[["PromptContext"], bool] | None = None
    snapshot: Callable[["PromptContext"], dict] | None = None
    render: Callable[["PromptContext"], str] | None = None
```

字段含义：

- `id`：稳定 ID，用于 trace、diff、测试和错误定位。
- `role`：注入给模型的消息角色。
- `scope`：片段所属层。
- `priority`：组装顺序，高优先级先渲染。
- `template_path`：模板文件路径。
- `marker`：片段边界标记。
- `budget`：片段最大预算。
- `required_vars`：严格渲染时必须提供的变量。
- `condition`：决定当前场景是否注入。
- `snapshot`：生成当前片段状态，用于 diff。
- `render`：自定义渲染函数。

## 分层边界

### Global Life

全局生命体层只描述开放框架：

```text
这是 AI 生命体。
程序只是骨架。
直接对话、工具结果、屏幕观察、记忆、调试输入都是信息通道。
思考强度、频率、说话、沉默、工具调用由 LLM 自主判断。
```

禁止写入：

- 具体角色关系。
- 具体人物名字。
- 具体工具细节。
- 针对某次 bug 的语义补丁。

### Character

角色层只处理当前角色的设定、人格、关系、认知、情绪。

例如雪吱：

```text
真冬是雪吱的开发者和监护者。
雪吱所在的电脑是真冬的电脑，也是她当前的生命载体。
```

这类内容不能出现在全局生命体提示词里。

### Runtime

运行状态层只提供事实材料：

- 当前时间。
- 距离上次思考多久。
- 新输入批次。
- 工具结果摘要。
- 当前 inner_stream 版本。
- 最近上下文摘要。

运行状态层不解释人格，不做心理结论。

### Tool

工具层只描述工具能力和工具调用上下文。

主循环里只注入工具短目录。完整工具提示词只在工具被选中或准备阶段加载。

### Debug

调试层只在 debug mode 中加入：

- 调试输入。
- trace 输出要求。
- 外部控制信息。

调试层不改变功能能力。

## Marker 规范

所有非自然输入都要有 marker。

建议格式：

```text
<life_contract>
...
</life_contract>

<character_profile id="lerwa">
...
</character_profile>

<inner_stream version="12">
...
</inner_stream>

<runtime_time>
...
</runtime_time>

<tool_catalog>
...
</tool_catalog>

<tool_result tool="search_web">
...
</tool_result>

<debug_input>
...
</debug_input>
```

作用：

- 让 LLM 区分材料和自然语言。
- 让程序识别已注入片段。
- 支持历史去重。
- 支持片段替换和 diff。
- 防止工具报告污染 inner_stream。

## Strict Renderer

模板使用简单占位符：

```text
{{ character_name }}
{{ inner_stream }}
{{ tool_catalog }}
```

渲染规则：

- 模板中出现的变量必须提供。
- 提供了模板未使用的变量时报错。
- 重复变量允许，但只提供一次值。
- 未闭合占位符时报错。
- 禁止静默替换为空字符串。

这能避免 prompt 修改后悄悄退化。

## Snapshot 和 Diff

每个可变片段都可以提供 snapshot。

示例：

```python
def environment_snapshot(ctx):
    return {
        "current_date": ctx.current_date,
        "timezone": ctx.timezone,
        "active_tools": sorted(ctx.active_tools),
        "debug_mode": ctx.debug_mode,
    }
```

组装时：

```text
1. 计算当前 snapshot。
2. 读取上一轮 snapshot。
3. 如果没有变化，不重复注入。
4. 如果变化，只注入变化片段。
5. trace 中记录 before / after / diff。
```

这适用于：

- 工具目录。
- 环境状态。
- 调试模式状态。
- 屏幕观察摘要。
- 记忆检索摘要。
- 当前对话通道状态。

`inner_stream.txt` 本身不应该被 diff 机制替代。它仍然是 AI 生命体当前内在叙事正文。diff 只用于管理提示词上下文，不取代 inner_stream。

## 组装流程

一次 life tick 的推荐流程：

```text
1. Runtime 收集事实，构造 PromptContext。
2. PromptRegistry 根据场景选择 fragments。
3. 每个 fragment 判断 condition。
4. 每个 fragment 生成 snapshot。
5. PromptState 对比上一轮 snapshot，决定 full render 或 diff render。
6. StrictRenderer 渲染模板。
7. BudgetManager 控制每个片段长度。
8. PromptManager 按 role 和 priority 组装 messages。
9. Diagnostics 写出完整 trace。
10. 调用 LLM。
```

输出结构：

```python
[
    {"role": "system", "content": "..."},
    {"role": "developer", "content": "..."},
    {"role": "user", "content": "..."},
]
```

## 工具 Prompt 规范

每个工具模块自带提示词，不放在全局大 prompt 中。

建议工具目录：

```text
kokoro/action/tools/search_web/
  manifest.toml
  prepare.md
  after.md
  spec.py
  prepare.py
  execute.py
  after.py
```

`manifest.toml` 示例：

```toml
id = "search_web"
name = "search_web"
description = "搜索公开网络信息"
needs_prepare_llm = true
prepare_prompt = "prepare.md"
after_prompt = "after.md"
```

主循环工具目录只注入短描述：

```text
- search_web: 搜索公开网络信息。需要 query。
- write_memory: 写入角色长期记忆。需要 trigger_text 和 memory_text。
- say: 对外说话。需要 text。
```

只有工具被选中时，才加载：

- `prepare.md`
- `after.md`
- 工具私有规则
- 工具输入输出 schema

这样可以保持主循环快速，不让本地小模型被工具长规则压垮。

## Debug Trace

debug mode 必须输出完整 prompt trace。

建议目录：

```text
test_runs/<run_id>/prompt_trace/
  000_tick/
    context.json
    selected_fragments.json
    snapshots_before.json
    snapshots_after.json
    rendered_system.md
    rendered_developer.md
    rendered_user.md
    llm_raw.txt
    parsed.json
    tool_plan.json
```

trace 中必须能回答：

- 当前注入了哪些片段。
- 每个片段来自哪里。
- 每个片段用了多少预算。
- 哪些片段因为 diff 未注入。
- 哪些片段因为预算被截断。
- LLM 原始输出是什么。
- 解析结果是什么。
- 工具选择链路是什么。

## 与现有 prompts/ 的关系

迁移期内保留现有 `prompts/`。

兼容策略：

```text
旧 prompts/*.toml -> PromptFragment wrapper -> 新 PromptManager
```

不要一次性重写所有 prompt。

迁移顺序：

1. 新建 `kokoro/prompt` 基础代码。
2. 接入 strict renderer 和 diagnostics。
3. 迁移 `life_runtime.toml`。
4. 迁移工具目录 prompt。
5. 迁移 memory prompt。
6. 迁移 dialogue prompt。
7. 迁移 vision prompt。
8. 删除旧兼容层。

## 验收标准

第一阶段验收：

- 可以通过 `PromptManager.render("life_tick", ctx)` 生成完整 messages。
- trace 能看到每个 fragment。
- 缺变量会报错。
- 多余变量会报错。
- debug mode 能导出完整 prompt trace。
- 不改变现有 life runtime 行为。

第二阶段验收：

- `life_runtime` 不再直接读取 `prompts/life_runtime.toml`。
- 工具 prepare / after prompt 属于各自工具目录。
- 工具目录主 prompt 只包含短描述。
- 角色专属设定不会进入全局提示词。
- 屏幕、工具、调试信息不会直接污染 inner_stream。

第三阶段验收：

- snapshot/diff 生效。
- 无变化的环境状态不重复注入。
- 工具目录过长时可按预算截断。
- prompt trace 可复现一次 LLM 调用。

## 设计底线

这套架构不能变成硬编码心理机制。

程序只做：

- 装配。
- 校验。
- 缓存。
- diff。
- 预算。
- 记录。
- 分发。

LLM 继续决定：

- 思考强度。
- 思考频率。
- 是否说话。
- 是否沉默。
- 是否调用工具。
- 如何理解角色关系。
- 如何延续 inner_stream。

也就是说，`kokoro/prompt` 是提示词工程的骨架，不是替代生命体自主思考的规则系统。
