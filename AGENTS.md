# Alice 项目开发规范

本文件是 Codex 在本仓库内工作的项目级默认指令。它只适用于 Alice 项目。

## 项目定位

Alice 是一个以内在叙事流为核心的本地 AI 生命体实验框架，不是普通 AI 助手项目。

核心方向：
- `inner_stream.txt` 必须保留，它是真正的自主决策和主体连续性痕迹。
- 程序只是骨架，负责输入、上下文、时间、记忆、工具执行、日志和边界。
- LLM 自主决策是核心。不要用程序硬分类、硬优先级、硬流程替代 LLM 的判断。
- 优先优化提示词和上下文边界，再考虑程序补丁。
- 思考速度、频率、短期上下文密度和时间观念非常重要。

## 架构原则

理想主链路：

信息事件 -> 内在叙事流 -> 行动意向 -> 工具选择 -> 工具 prepare -> 工具 execute -> 工具 after -> 信息事件/记忆

开发时遵守：
- CLI 只作为入口和运行时组装，不承载业务逻辑。
- 项目对外统一入口是 `cli.py`。不要新增并列启动入口；需要不同输出或调试形态时，用 `cli.py` 参数切换。
- 工具、输入源、直播、VTS、QQ、主动说话、屏幕观察等都应拆到对应模块。
- 除内在叙事流之外的外部能力都视为工具或运行时能力。
- 工具相关提示词只出现在工具选择、工具 prepare 或工具 after 中，不要污染 life tick。
- 工具结果不是事实本身，而是进入生命流的材料；必要时必须先由 after 消化。
- `action_result`、执行状态、日志字段、schema、URL 列表、候选标题等不能直接污染生命流。
- 运行态反馈要可审计，但 debug 日志和模型可见上下文必须分开。

## 提示词优先

遇到行为问题时，先判断是不是提示词和上下文边界问题。

优先顺序：
1. 提示词是否表达了正确的生命体身份、注意力机制和输出契约。
2. 上下文是否混入了工具名、日志、schema、状态回执、调试文本或无关材料。
3. 工具 prepare/after 是否把工具专属推理留在工具内部。
4. 程序是否只是提供结构边界，而不是替 LLM 做语义决策。

避免：
- 用程序分类“兴趣、专注、情绪、工具等级、优先级”来控制生命活动。
- 为某个角色写全局硬编码。角色私有设定必须留在角色数据或角色提示词中。
- 在通用提示词里写雪吱、真冬、yuki 等特定关系。
- 在 life tick 中提具体工具名或工具行为。
- 因一次测试现象就加末端补丁掩盖根因。

可以接受的程序约束：
- JSON/协议归一化。
- 去重、限长、上下文边界、debug 隔离。
- 工具执行安全边界。
- 把 raw result 从模型可见上下文移到 debug。
- 防止重复文本刷屏的结构层保护。

## 内在叙事流

`inner_stream.txt` 是主体意识连续文本，不是日志、任务清单、状态表或工具结果记录。

开发要求：
- inner_stream 更新应使用 patch 思路，避免全量覆盖。
- 只有真实内在变化才写入，包括注意力移动、迟疑、决定、情绪波动、未完成线索、对材料的吸收。
- 不要把执行回执、成功失败、结果数量、日志字段、文件名、schema、URL、候选标题写入 inner_stream。
- 重复或近重复 append 应被阻止。
- 工具调用可以频繁，但不能让工具状态推动生命流机械循环。
- 思考不一定要行动。连续思考、等待、消化失败、缩小问题都是正常生命活动。

## 记忆系统

角色真正的长期价值来自记忆。记忆要多、召回要准、能支撑连续生命活动。

当前方向：
- 记忆是核心系统的一部分，不应被当成普通外部工具。
- 所有进行中的经历都可以先进入经验工作区，再由生命周期线程沉淀、整合、遗忘。
- 不重要内容可以之后遗忘，不要因为害怕膨胀而过早丢失生命活动。
- 记忆不按用户域、场景域等维度硬分；以角色为核心。
- 召回可以频繁，但提示词要让记忆自然影响判断，不要频繁显式说“我突然想起”。
- 扩散效应是合理方向：记住或想起一件事时，可以带出相近时间和相关线索，但要有衰减。

避免：
- 把调试 tick、schema、工具注册列表、程序状态、空 wait、机械日志写成长期记忆。
- 把无效搜索、词典页、娱乐平台、泛泛候选当成角色认知事实。
- 把事件直接写成人物定性；认知层和记忆层要分清。

## 工具模块规范

行动工具位于 `kokoro/action/tools/<tool_name>/`。

注册工具目录应遵守：
- `spec.py` 注册工具。
- `execute.py` 执行能力。
- `prepare.py` 可选，做工具执行前的参数提炼或额外 LLM 调用。
- `after.py` 可选，做工具执行后的消化、记录或反馈。
- `manifest.toml` 描述工具。

工具职责：
- 行动选择器只决定是否需要某类能力。
- 工具专属 query 提炼、记忆整合、说话塑形、搜索结果消化等，放在对应工具的 prepare/after。
- 需要额外 LLM 调用的工具，在 prepare 或 after 内调用，不要塞进通用选择器。
- 工具 raw 输出默认进 debug；模型可见内容必须经过边界处理。
- 跨工具访问通过工具包门面，不导入其他工具内部实现。

仅运行时能力也可放在 `tools/` 下，但必须列在 `RUNTIME_MODULE_NAMES`，且不能暴露注册工具的 `spec.py`。

## 统一运行入口

所有生命体运行和调试都应从 `cli.py` 进入。

推荐入口：
- 完整生命体运行：`python cli.py -c <character>`
- 无麦克风/无语音调试：`python cli.py -c <character> --no-stt --no-tts --debug-input`
- 文本输出模式：`python cli.py --output-mode text -c <character>`
- LifeRuntime trace 调试：`python cli.py --output-mode life-debug -c <character> --ticks 3`
- 长时间真实 LLM 生命周期测试：`python cli.py --life-debug -c lerwa --duration-seconds 600 --real-llm`
- 多角色模式：`python cli.py --multi alice,penglai`

兼容入口可以存在，但只能转发到 `cli.py`：
- `text_cli.py` 应转发到 `python cli.py --output-mode text ...`
- `scripts/run_life_runtime_debug.py` 应转发到 `python cli.py --output-mode life-debug ...`
- `run_multi.py` 应转发到 `python cli.py --multi ...`

不要让兼容脚本维护独立参数、独立运行时组装或独立业务逻辑。新增模式时，优先扩展 `kokoro/action/cli_common.py` 的参数和 `kokoro/action/cli_runtime.py` 的分发。

## 提示词管理

提示词放在 `kokoro/prompt/templates/`。

要求：
- 默认使用中文提示词，尤其是本地 7B 模型参与的提示词。
- 短句、明确职责、明确输出契约。
- 不要把工具 catalog、schema、字段名暴露给不需要的场景。
- life 层不出现具体工具名；tool_select 层才出现可用能力；工具 prepare/after 层才出现工具细节。
- 输出 JSON 的提示词必须明确只输出 JSON object，不要 Markdown 和解释。
- 修改提示词后要跑 debug 测试，查看 trace，不只看控制台输出。

## 调试与测试

常用调试入口：
- `python cli.py --output-mode life-debug -c lerwa --ticks 3`
- `python cli.py --life-debug -c lerwa --duration-seconds 600 --real-llm`
- `python cli.py -c lerwa --no-stt --no-tts --debug-input`
- `scripts/analyze_life_runtime_debug.py <run_dir>` 只负责分析已有运行记录，不作为生命体启动入口。

测试原则：
- 重要改动后至少跑短测 smoke。
- 生命周期、记忆、工具边界改动后，优先跑 10-20 分钟 debug。
- 测试时使用角色 `lerwa` 代表雪吱。
- 本地模型测试优先使用 `qwen2.5:7b`，不要用 1.5B 判断复杂生命流质量。
- 调试模式只是更容易外部干涉、日志更完整；功能应与真实运行一致。

关注指标：
- tick 数和每分钟 tick 率。
- action rejected/error 数。
- action_result 是否污染 context。
- raw 工具结果是否进入 life prompt。
- search query 是否重复换词。
- inner_stream 是否重复 append。
- memory store 是否沉淀，是否重复，是否有认知变化。

## 文件与运行态

不要提交本地运行态：
- `characters/`
- `debug_runs/`
- `test_runs/`
- `mem0_data/`
- `data/`
- `models/`
- `__pycache__/`
- overlay state、subtitle state、token、本地 config。

`characters/` 整体是本地角色、画像、记忆和运行态，默认 ignore。角色设定样例或文档需要进入仓库时，应放到 `doc/`、`config/` 示例文件或专门 fixtures，而不是直接提交本地角色目录。

## 开发风格

- 优先遵循现有模块边界和命名风格。
- 不做无关重构。
- 不回滚用户已有改动。
- 编辑文件用 `apply_patch`。
- 搜索优先用 `rg`。
- Windows 下删除目录前确认目标在工作区内。
- 清理目录时只删明确的缓存、debug 输出、运行态；不要删除源码、文档、配置示例。

## 提交前自查

完成改动后检查：
- `git status --short`
- 必要的 `python -m py_compile ...`
- 必要的 debug smoke
- `.gitignore` 是否覆盖新增运行态
- 是否误把角色私有文件、记忆库、调试输出加入 git
- 是否把角色特定关系写进通用提示词
- 是否用程序硬编码替代了 LLM 自主判断
