# Codex 专注连续性参考落实记录

本文记录 `doc/codex_focus_continuity_reference.md` 在 Alice 当前架构中的落实状态。原则不变：程序只是生命现场的骨架，核心是 LLM 自主决策；程序只维护来源、时间、边界、流速、记录和工具执行，不替角色做语义分类或重要性判断。

## 当前落实状态

### 主意识现场

`LifeRuntime` 已作为主意识现场承载生命 tick。`ChatSession` 在 life runtime 为 primary 时不再启动旧的 `InnerStreamLoop` 和 `AutonomousStep`，避免旧循环与新生命现场同时推着角色行动。

工具执行边界没有继续绑在旧 `AutonomousStep` 上。`LifeRuntime` 通过独立的 action runtime 装配注册工具，LLM 在主意识现场选择工具，`ActionRuntime` 只负责执行、发布反馈和调用工具自己的 prepare/execute/after 生命周期。这样 primary 模式既不会恢复旧的自主决策循环，也不会失去工具能力。

代码证据：

- `kokoro/core/chat_session.py`
- `kokoro/action/life_runtime.py`
- `kokoro/life/runtime.py::LifeRuntime._create_action_runtime`
- `tests/test_life_runtime.py::test_chat_session_life_runtime_primary_skips_old_autonomous_loop`
- `tests/test_life_runtime.py::test_chat_session_primary_life_runtime_keeps_tool_runtime_without_old_loop`

### 工具结果同现场回流

工具计划执行后，结果会在同一次 life tick 内回到 `_think()`，作为 `same_tick_tool_results` 进入上下文。LLM 可以在同一个意识现场继续理解工具结果、修改 `inner_stream`、继续调用工具或放下，不需要等下一轮冷启动。

`search_web` 的结果文本已经改成中性结构化格式 `[web_search_result]`，不再用“我刚刚搜索了……”这类第一人称回执开头。这个改动不替 LLM 总结搜索内容，只减少工具表面文本对 inner_stream 的污染。

代码证据：

- `kokoro/life/runtime.py::_continue_after_action_results`
- `kokoro/life/runtime.py::_absorb_action_results_context`
- `kokoro/action/tools/search_web/client.py::format_search_result`
- `tests/test_life_runtime.py::test_life_runtime_feeds_tool_results_back_in_same_tick`
- `tests/test_life_runtime.py::test_web_search_result_format_is_neutral_not_first_person`

### 输入池与时间等待感

外部输入进入 `InformationPool`，life tick 从池中取批次。事件文本现在带有 `age`，时间意识也会收到当前批次的等待时间摘要，例如这批事件最早等了多久、最新等了多久、序号范围是什么。

这只是时序事实，不是语义分类。它的目的不是让程序决定事情重要不重要，而是让 LLM 能知道“信息已经等了多久”，从而自己决定节奏。

代码证据：

- `kokoro/life/event_pool.py::InformationPool.format_batch`
- `kokoro/life/event_pool.py::InformationPool.timing_lines`
- `kokoro/life/runtime.py::tick_once`
- `tests/test_life_runtime.py::test_life_runtime_time_context_includes_event_batch_age`

### 有界上下文碎片

动态上下文通过 `life_context` 片段进入 prompt，每个片段带 source、created_at、max_chars。程序只标注来源和边界，不判断内容意义。

工具 prompt manifest 现在始终从项目源码目录读取，不受 debug run 输出目录影响。LifeRuntime 的“可用工具能力”只展示当前可用 action、工具用途、prepare/after LLM 阶段标记和 schema，不把不可用 action 暴露给 LLM。

主意识 prompt 默认不再注入完整 prepare/after 文本。完整阶段提示词仍由工具自己的 prepare/execute/after 生命周期使用；主意识只需要知道“有什么能力”和“参数边界”。这是为了避免本地 7B 在 life tick 中被工具阶段提示污染，导致 JSON 契约失败、复制 say/search 的 after 文案，或把工具回流说明误写进 inner_stream。需要诊断阶段提示词时，可以显式打开 `include_tool_stage_prompts_in_life_prompt`。

工具结果和输入池也继续收紧为“材料”而不是“可复制正文”。输入池现在用 `<input_event ...>` 片段呈现来源、时间、等待时长和内容，避免旧的 `[#序号 时间 source]` 日志行被模型当作 inner_stream 文本复制。主意识 prompt 明确要求：输入池包装、高密度上下文、pending threads、记忆材料和工具候选结果都不能原样搬进 inner_stream，只能转化成此刻注意力的变化。

`search_web` 输出也改成短候选材料：保留 query、候选数量、标题、URL 和短 note，不再附带长 snippet、query_match_hint 或大段网页条目。主意识中的 `event_batch` 和 `tool_results_digest` 片段分别有更小的默认上限，避免搜索结果同时通过 live event 和 tool digest 双重放大。

代码证据：

- `kokoro/life/context_fragments.py`
- `kokoro/life/runtime.py::_think`
- `kokoro/life/runtime.py::_tool_capabilities_text`
- `kokoro/prompt/tools.py::render_tool_catalog`
- `tests/test_life_runtime.py::test_life_runtime_context_fragments_bound_dynamic_prompt_material`
- `tests/test_life_runtime.py::test_life_runtime_loads_tool_prompt_specs_from_project_root`
- `tests/test_life_runtime.py::test_life_runtime_can_opt_in_to_tool_stage_prompts_for_diagnostics`
- `tests/test_life_runtime.py::test_web_search_result_format_keeps_prompt_material_compact`

### 显式上下文压缩

短期现场由 `ContextCompactor` 显式压缩。压缩会写入 `recent_digest.txt`，同时追加 `compaction_audit.jsonl`，记录输入字符量、输出字符量、实现方式和相关路径，便于诊断长期连续运行。

代码证据：

- `kokoro/life/context_compactor.py::compact_once`
- `kokoro/life/context_compactor.py::_append_compaction_audit`
- `tests/test_life_runtime.py::test_context_compactor_writes_explicit_compaction_audit`

### 记忆核心化

记忆不再暴露为普通 action 工具让 life tick 调用。生命现场会把输入、思考、工具结果、上下文摘要等持续沉积给 memory system；记忆系统作为核心生命周期的一部分运作，而不是外部工具。

记忆召回进入主意识现场前会做提示词安全展示：优先使用 LLM 沉淀出的摘要，同时保留清理后的具体内容线索；旧 JSON 包装、工具回执样式、嵌套的“刚被带出的记忆材料”样板不会原样回灌到 prompt。这个处理只改变展示边界，不改变记忆存储、召回排序或重要性判断。

经验工作区现在明确标记为“记忆材料，不是当前现场”，并带 `updated_at`。提示词也明确：当前 inner_stream、新进入信息和时间感更贴近此刻；旧记忆或经验工作区与当前现场冲突时，不应替主体决定方向。

调试脚本也遵守这个边界：旧的强制记忆工具诊断入口已经移除。调试时如果需要给记忆系统提供材料，使用 `--memory-event seconds:text` 注入一条记忆候选输入，让它进入同一个生命现场和核心记忆循环，而不是绕过现场直接调用 `search_memory` 或 `save_to_memory`。

代码证据：

- `kokoro/life/runtime.py::_append_memory_event`
- `kokoro/life/runtime.py::_run_memory_core_cycle`
- `kokoro/memory/recall.py::_record_prompt_text`
- `kokoro/memory/service.py::MemorySystem.default_context`
- `kokoro/memory/workspace.py::MemoryWorkspaceState.as_context`
- `scripts/run_life_runtime_debug.py`
- `tests/test_life_runtime.py::test_life_runtime_rejects_unavailable_or_incomplete_action_plan`
- `tests/test_life_runtime.py::test_life_runtime_memory_is_core_not_action_tool`
- `tests/test_life_runtime.py::test_life_debug_script_uses_memory_candidate_inputs_not_forced_memory_tools`
- `tests/test_memory_life_system.py::test_recall_formats_memory_as_prompt_safe_material`

### 本地模型优先队列

`LocalThinking` 提供本地模型调用队列，按调用类型排序。这里的排序只服务流速：life tick 高于压缩和记忆维护，避免低优先级后台整理长期阻塞主意识现场。它不做抢占，也不替 LLM 判断语义重要性。

代码证据：

- `kokoro/life/local_thinking.py`
- `tests/test_life_runtime.py::test_local_thinking_priority_queue_runs_life_tick_before_memory`

## 当前仍需继续验证

这些点还不能只靠单元测试证明，需要继续用长时间 debug run 验证：

- 10 到 20 分钟运行中，life tick 数量是否明显恢复，不再长期停在少数几次完整 tick。
- 工具结果是否能稳定在同一现场被理解，而不是写进日志后才在下一轮被冷启动处理。
- 时间意识是否真的改善等待、拖延、未完成牵挂的连续性。
- 记忆沉积是否足够多，且默认召回不会把近期摘要刷屏。
- 本地模型队列是否只是排序，不产生抢占、饥饿或后台任务无限堆积。

## 运行态审计

新增只读审计脚本：

```powershell
python scripts/analyze_life_runtime_debug.py <run_dir> --write
```

它读取 `run_summary.json`、`lifecycle_trace.jsonl`、`characters/*/context/compaction_audit.jsonl` 和 `prompt_trace/`，输出 `continuity_audit.json`。脚本只统计运行事实，不对角色内容做语义分类，主要指标包括：

- tick 数和每分钟 tick 率。
- 已处理输入数量。
- inner_stream patch 次数。
- action plan 执行、拒绝、错误数量。
- 同 tick 工具反馈是否出现。
- prompt trace 数量。
- 显式压缩审计记录数量。
- 记忆核心循环和 memory candidate 输入数量。
- 本地模型队列 queued/start/done/coalesced 数量。
- 错误事件列表。

代码证据：

- `scripts/analyze_life_runtime_debug.py`
- `tests/test_life_runtime.py::test_life_debug_analyzer_reports_continuity_evidence`

短 scripted 验证：

```powershell
python scripts/run_life_runtime_debug.py --character lerwa --ticks 2
python scripts/analyze_life_runtime_debug.py test_runs\life_runtime_debug_lerwa_scripted_20260706-015539-594766 --write
```

结果证明审计链路能看到：

- `tick_count`: 2
- `same_tick_tool_feedback.present`: true
- `same_tick_tool_feedback.context_events`: 1
- `context_continuity.compaction_audit_records`: 2
- `context_continuity.prompt_trace_dirs`: 3
- `errors.count`: 0

这个 scripted run 只能证明骨架链路，不证明真实 LLM 长时间生命活动质量。

最新 scripted 验证：

- `test_runs/life_runtime_debug_lerwa_scripted_20260706-024519-871902`
  - `tick_count`: 2。
  - `action_plan.executed`: 1。
  - `same_tick_tool_feedback.present`: true。
  - `context_continuity.prompt_trace_dirs`: 3。
  - `context_continuity.compaction_audit_records`: 2。
  - `errors.count`: 0。
  - 这次验证还覆盖了 primary life runtime 使用独立 action runtime 装配工具的路径。
- `test_runs/life_runtime_debug_lerwa_scripted_20260706-024946-222968`
  - `tick_count`: 2。
  - `action_plan.executed`: 1。
  - `same_tick_tool_feedback.present`: true。
  - `context_continuity.prompt_trace_dirs`: 3。
  - `context_continuity.compaction_audit_records`: 2。
  - `errors.count`: 0。

真实运行验证记录：

- `test_runs/life_runtime_debug_lerwa_real_20260706-015742-603504`
  - 10 分钟。
  - `tick_count`: 298，`tick_rate_per_minute`: 29.4。
  - `same_tick_tool_feedback.present`: true，`context_events`: 14。
  - `errors.count`: 0。
  - 问题：研究对象从 Minecraft 冒险模组漂到“全球顶尖设计案例”，inner_stream 大量重复工具回执式句子。
- `test_runs/life_runtime_debug_lerwa_real_20260706-021008-580430`
  - 5 分钟，加入提示词修正后。
  - `tick_count`: 177，`tick_rate_per_minute`: 34.809。
  - `same_tick_tool_feedback.present`: true。
  - `errors.count`: 0。
  - 改善：主题保持在 Minecraft 相关方向。
  - 问题：web search 工具输出仍以“我刚刚搜索了……”污染 inner_stream。
- `test_runs/life_runtime_debug_lerwa_real_20260706-022213-818345`
  - 2 分钟，web search 输出改为中性 `[web_search_result]` 后。
  - `tick_count`: 56，`tick_rate_per_minute`: 27.019。
  - `same_tick_tool_feedback.present`: true，`context_events`: 4。
  - `local_thinking_queue.started == done == 79`。
  - 改善：inner_stream 不再直接复制“我刚刚搜索了/搜索结果：”。
  - 剩余问题：仍偏向 Fabric/Forge 兼容性，pending_threads 没稳定出现，有 1 次 action_plan 结构错误。
- `test_runs/life_runtime_debug_lerwa_real_20260706-022822-663255`
  - 2 分钟，补充 action_plan 字段契约与 pending_threads 提示后。
  - `tick_count`: 60，`tick_rate_per_minute`: 29.977。
  - `action_plan.executed`: 3，`action_plan.error`: 0。
  - `same_tick_tool_feedback.present`: true，`context_events`: 3。
  - `pending_threads_chars`: 19。
  - `local_thinking_queue.started == done == 75`。
  - 改善：malformed action_plan 消失，pending_threads 出现，主题保持在 Minecraft 冒险模组附近。
  - 剩余问题：inner_stream 仍会生成“我刚刚搜索了/刚才搜索”等回执语气，虽然不再来自工具原文；已继续补充“问题如何变化”的正反例提示词，待下一轮真实运行验证。
- `test_runs/life_runtime_debug_lerwa_real_20260706-025058-135186`
  - 3 分钟，加入记忆提示词安全展示后。
  - `tick_count`: 54，`tick_rate_per_minute`: 17.921。
  - `same_tick_tool_feedback.present`: true，`context_events`: 7。
  - `action_plan.executed`: 7，`errors.count`: 0。
  - 问题：search query 仍从 Minecraft 冒险模组漂到“战利品节奏设计指南”等泛词，搜索结果进入电视剧、词典、域名页；inner_stream 仍出现“我刚刚搜索了”和连续“需要确认搜索是否真的没有结果”。
  - 处理：收紧 search_web prepare/after 提示，要求保留当前具体对象；context compact 遇到跑偏搜索只保留“query 太泛/需要换来源”的影响；inner_stream 提示禁止把确认搜索失败反复写成内在叙事。
- `test_runs/life_runtime_debug_lerwa_real_20260706-025814-040033`
  - 2 分钟，加入 search query 和压缩提示修正后。
  - `tick_count`: 48，`tick_rate_per_minute`: 23.008。
  - `same_tick_tool_feedback.present`: true，`context_events`: 2。
  - `action_plan.executed`: 1，`errors.count`: 0。
  - 改善：搜索次数从 9 次降到 2 次，重复搜索明显减少；第二个 query 保留了“冒险模组/地牢推进/模组页面”等具体对象。
  - 剩余问题：旧 Fabric/Forge 经验工作区仍牵引当前现场，inner_stream 仍出现“我刚刚搜索了”和重复确认句。已继续修正：工具 prompt manifest 改为从项目根读取，经验工作区明确标记为旧记忆材料，提示词强调当前现场优先于旧记忆。
- `test_runs/life_runtime_debug_lerwa_real_20260706-031223-617483`
  - 2 分钟，主意识 prompt 默认移除完整 prepare/after 后。
  - `tick_count`: 66，`tick_rate_per_minute`: 32.979。
  - `action_plan.executed`: 1，`errors.count`: 0。
  - 改善：此前 43 次 `thought_parse_failed` 的崩坏消失，只剩 1 次 repair；工具阶段提示没有继续泄漏到 raw 输出。
  - 问题：提示词中的负面例句被模型复读，inner_stream 反复出现搜索失败确认句。
  - 处理：删除 life prompt 和 compact prompt 中可复制的负面例句、正例整句，改成抽象边界。
- `test_runs/life_runtime_debug_lerwa_real_20260706-031625-352620`
  - 60 秒，删除可复制反例后。
  - `tick_count`: 28，`tick_rate_per_minute`: 26.321。
  - `action_plan.executed`: 2，`errors.count`: 0。
  - 改善：负面例句复读基本消失。
  - 问题：模型复制输入池原文；搜索结果候选里的“魔兽世界/模拟器”把当前主题拖走。
  - 处理：输入池格式改为 `<input_event ...>` 材料块；web search 输出改为短候选材料；主意识 prompt 明确不要复制输入池包装、高密度上下文和工具候选结果。
- `test_runs/life_runtime_debug_lerwa_real_20260706-032001-584085`
  - 60 秒，输入池 XML 化和短搜索候选后。
  - `tick_count`: 14，`tick_rate_per_minute`: 13.956。
  - `action_plan.executed`: 2，`same_tick_tool_feedback.present`: true。
  - 改善：query 没有漂到魔兽世界或模拟器。
  - 问题：第一 tick 仍尝试复制事件包装并触发 1 次 parse failure；后续搜索结果仍可能把“暗黑地牢”等候选词带进当前现场。
  - 处理：继续收紧压缩提示，要求没有真实工具结果时不得编造搜索、query、搜索跑偏或换来源；降低主意识中工具结果片段上限。
- `test_runs/life_runtime_debug_lerwa_real_20260706-032432-247549`
  - 60 秒，压缩提示未收紧前的一次失败验证。
  - `tick_count`: 15，`tick_rate_per_minute`: 13.312。
  - 问题：初始压缩在没有工具结果时幻觉写出“搜索跑偏、query 太泛”，随后触发泛 query，搜索结果把主题拖到歌词、百科和公文。
  - 处理：压缩提示明确“没有工具结果就不能写工具经历或搜索经历”；主意识工具结果片段默认上限降到 1400，event batch 默认上限降到 2400。
- `test_runs/life_runtime_debug_lerwa_real_20260706-032849-005813`
  - 60 秒，压缩提示和片段上限修正后。
  - `tick_count`: 36，`tick_rate_per_minute`: 35.104。
  - `action_plan.executed`: 0，`errors.count`: 0。
  - 改善：初始压缩不再凭空写搜索跑偏；没有复制 `<input_event>` 包装；主题保持在 Minecraft 冒险模组、地牢推进、战利品节奏附近。
  - 剩余问题：有一次把高密度上下文里的项目符号摘要原样写进 inner_stream；已继续补充提示，要求高密度上下文和 pending_threads 只能作为材料，不能原样搬运。

这些真实运行证明“连续运行速度、同现场工具回流、可审计日志”已经成立，但还不能证明生命活动质量完全达标。剩余问题主要是提示词和工具反馈边界：如何让她更稳定地保留当前研究对象、把未完成线索放入 pending_threads，并减少错误 action_plan。

## 验证命令

当前相关单元测试已通过：

```powershell
python -m unittest tests.test_life_runtime tests.test_memory_life_system tests.test_prompt_management
```

最近一次结果：

```text
Ran 77 tests in 44.223s
OK
```
