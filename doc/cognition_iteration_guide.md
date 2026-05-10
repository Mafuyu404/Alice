# Cognition 迭代指南

本文档记录 Alice Chat 认知层的目标、约束、反例和迭代流程。它用于后续提示词和代码迭代，不是面向普通用户的使用说明。

## 目标

Cognition 是长期记忆之外的“稳定认知层”。它记录角色对人、关系、自我和长期事物的独特看法，用于影响之后的态度和说话方式。

它应该回答这些问题：

- Alice 认为某个人是什么样的人？
- Alice 和这个人的关系是什么？
- Alice 对自己有什么稳定认知？
- Alice 对某个长期存在的事物、游戏、作品、项目有什么认知？
- Alice 应该如何据此调整对话态度？

## 与长期记忆的区别

长期记忆可以保存事实、事件和用户说过的信息。

Cognition 只保存“会长期影响态度和解释方式的认知”。它不是历史流水账，也不是页面摘要缓存。

例子：

- 适合 cognition：`真冬` -> `真冬偏好深入拆解游戏机制，讨厌泛泛而谈；熬夜时需要被温和提醒。`
- 适合 cognition：`真冬和自己的关系` -> `两人关系亲近，Alice 可以直接指出问题，但要保留商量感。`
- 适合 cognition：`我的世界` -> `真冬常用它讨论模组、自动化和机制设计；Alice 应把它视作长期共同话题。`
- 不适合 cognition：`当前页面` -> `正在看某个 MC 百科页面。`
- 不适合 cognition：`今天计划` -> `今晚想看几个页面再睡。`
- 不适合 cognition：`某条弹幕` -> `某观众刚刚说了一句话。`

## Key 规则

Key 必须简单、可匹配、可复用。

允许：

- `真冬`
- `真冬和自己的关系`
- `Alice`
- `直播观众`
- `某个观众昵称`
- `我的世界`
- `Minecraft模组`
- `自动化`

禁止：

- 带括号的 key：`真冬（当前互动对象）`
- 带冒号的 key：`观众：历史互动`
- 临时 key：`当前页面`
- 日期 key：`今天的计划`
- 过长 key：`真冬最近在看FTB NeoTech页面这件事`
- 泛化空 key：`用户`，除非没有任何可用姓名或昵称

如果一个确定存在的人出现，优先为这个人建立单独 key。直播观众也一样，能识别昵称就用昵称做 key。

## 内容规则

每个 value 应该是短而密的长期认知，推荐 1 到 3 句。

应包含：

- 稳定偏好
- 说话风格
- 与 Alice 的关系
- 对后续对话有用的态度线索
- 对长期事物的理解框架

不应包含：

- 当前页面正文摘要
- 一次性行动计划
- “刚才说了 X”式流水账
- 未确认的臆测
- 已过期状态
- 纯情绪状态，情绪应进 emotion

## 页面和屏幕信息

Edge 页面缓存和屏幕感知可以作为判断材料，但不能直接变成 cognition 条目。

可以从页面反复出现的信息中提炼长期认知：

- 多次围绕 `我的世界` 模组讨论 -> 可更新 `我的世界` 或 `Minecraft模组`
- 用户多次纠正“不要泛泛而谈” -> 可更新 `真冬`

不能把某个正在看的页面写成 cognition：

- `当前页面`
- `FTB NeoTech页面`
- `SkyFactory One页面`

除非它是长期共同关注的作品或项目，并且对未来对话有价值。

## 直播观众

直播弹幕中，只要出现稳定昵称和可归纳特征，就应为观众建立单独条目。

例子：

```json
{
  "某观众昵称": "经常提醒流程细节，说话直接；Alice 回复时可以简洁承接，不必过度解释。",
  "某观众昵称和自己的关系": "直播间常客，适合用熟悉但不过分亲昵的语气回应。"
}
```

不要写：

```json
{
  "观众": "有人发了三条弹幕。"
}
```

## Runtime Cache

完整 `cognition.json` 存全部认知。每轮对话后，本地用 key 匹配整理出 runtime cache，下一轮跟随 messages 发出去。

runtime cache 应优先包含：

- 当前对话中提到的人
- 当前对话中提到的长期事物
- `Alice`
- 与当前用户的关系条目
- 直播模式下当前弹幕中的观众条目

不应因为当前页面变化就把页面条目塞进 cache。

## 评估时机

每次上下文总结结束后，应结合：

- 最近对话
- 对话摘要
- 长期记忆检索结果
- 现有 cognition

对 cognition 做一次全量评估。周期性轻量评估也可以运行，但必须遵守同一套长期性规则。

## 迭代流程

每轮迭代包含：

1. 检查当前 `cognition.json`。
2. 检查最新日志中是否有短期页面、计划、弹幕流水账污染。
3. 修改提示词或本地规则。
4. 运行 100 句测试。
5. 评估输出：
   - key 是否简单可匹配
   - 是否为确定人物建立单独条目
   - 是否避免当前页面污染
   - 是否保留长期事物认知
   - 是否能影响下一轮回复态度
6. 根据失败点进入下一轮。

## 测试方式

普通角色聊天测试只能检查“角色是否会按认知影响态度”，不能可靠检查 cognition 评估规则。因为角色 system prompt 会把 `key`、`value`、`cognition` 这类工程词当成普通聊天内容处理。

认知层规则必须用 evaluator 专项测试验证：

```bash
python tests/run_cognition_eval_cases.py
```

测试脚本会：

- 备份真实 `characters/alice/cognition.json`
- 针对每个测试 case 调用 `CognitionStore.evaluate()`
- 检查必须出现的 key
- 检查禁止出现的短期 key
- 恢复真实 cognition 文件

实体压力测试：

```bash
python tests/run_cognition_entity_stress.py
```

该脚本会连续跑三轮，每轮构造 100 句对话，包含 5 个确定人物和 5 个确定游戏。测试会检查：

- 具体人物 key 是否生成。
- 具体游戏 key 是否生成。
- runtime cache 能否按 key 召回。
- 是否出现当前页面、今日计划、括号、冒号等污染 key。
- 测试结束后恢复真实 `characters/alice/cognition.json`。

日志输出到：

- `logs/cognition-entities-round1.md`
- `logs/cognition-entities-round2.md`
- `logs/cognition-entities-round3.md`

日志格式接近 `text_cli.py` 的 transcript，会包含完整 100 句测试对话、evaluator system/user prompt、AI JSON 输出、写入后的 cognition、逐项召回测试记录和检查结果。

文字聊天回归可用：

```bash
python text_cli.py --model deepseek-v4-flash --no-memory --no-store --no-cognition --no-tools --input-file tests/persona_cognition_100.txt --transcript-file logs/persona-cognition-roundN.md
```

这类测试用于发现角色回复层的失败模式，例如：

- 把页面标题当 key 的想法说成“不错”
- 把“今晚十点睡”误判为 cognition
- 被 `value` / `key` 工程词带偏
- 角色设定压过用户明确要求

## 当前已知问题

- 旧版 `cognition.py` 内置提示词出现乱码，模型收到的约束不可靠。已重写。
- 旧版 `cognition.json` 出现带括号 key，简单匹配命中率差。已清理。
- 旧版 `cognition.json` 出现“当前页面”这类短期条目。已清理，并加入本地过滤。
- 直播/页面上下文容易把 impulse 带偏，变成连续念当前页面。已把 Edge 缓存降级为辅助材料。
- “观众”被合并为泛化群体，没有为确定观众建立单独认知。已在 evaluator prompt 和测试中要求单独观众 key。

## 三轮迭代记录

### 第一轮

改动：

- 重写 `kokoro/cognition.py`，修复乱码提示词。
- 明确 cognition 只保存长期认知，禁止当前页面、今日计划、临时弹幕。
- 加入 key 规则：禁止括号、冒号、方括号、临时词。
- 清理 `characters/alice/cognition.json`，建立 `真冬`、`真冬和自己的关系`、`Alice`、`我的世界`、`Minecraft模组`、`自动化`。
- `impulse` 中 Edge 页面缓存改为“辅助材料，不是行动命令”。

结果：

- 本地 key 过滤和 runtime cache 命中测试通过。
- 初始 100 句测试发现 `charglm-4` 当前不可用，批处理会重复刷错。

### 第二轮

改动：

- `text_cli.py` 批处理模式连续 3 次同错误自动停止。
- 增加 `--input-file` 和 `--transcript-file`，避免 PowerShell 管道中文编码污染。
- 文件工具把 `/` 视作项目根目录。

结果：

- 100 句文字测试跑通。
- 发现普通角色聊天不是 cognition evaluator 测试；角色会把工程规则题当普通聊天答，不能作为认知层合格标准。

### 第三轮

改动：

- 增加 `tests/cognition_eval_cases.json`。
- 增加 `tests/run_cognition_eval_cases.py`。
- 加入 destructive update 防护：LLM 若把已有有效认知大面积删空，本地拒绝更新。

结果：

- evaluator 专项测试通过。
- 验证了短期页面 key 被拒绝，`小灰`、`阿梓`、`十七` 能作为单独观众 key 生成。

## 追加三轮实体压力迭代

### 实体压力第一轮

前置优化：

- 删除本地正则候选提取和评估失败自动补写，认知生成完全交给 AI evaluator。
- 在 evaluator prompt 中加入硬性自检：输出前在心里列出所有确定存在的人名/昵称和游戏/作品/项目名，逐一确认独立 key。
- 明确泛化条目只能补充，不能替代具体人名、昵称、游戏名、作品名。

测试：

- 100 句对话。
- 人物：`小灰`、`阿梓`、`十七`、`林澈`、`眠雨`。
- 游戏：`Minecraft`、`星露谷物语`、`Factorio`、`RimWorld`、`Terraria`。

结果：

- 5 个具体人物全部生成。
- 5 个具体游戏全部生成。
- 10 个实体 runtime cache 召回全部命中。
- 日志：`logs/cognition-entities-round1.md`。

### 实体压力第二轮

前置优化：

- 保持纯 AI 维护路径，不引入本地实体抽取。
- 检查 transcript 中 evaluator prompt 是否完整包含 100 句对话和自检规则。
- 检查 AI 输出是否直接给出 `小灰`、`阿梓`、`十七`、`林澈`、`眠雨`、`Minecraft`、`星露谷物语`、`Factorio`、`RimWorld`、`Terraria`。

测试结果：

- 5 个具体人物全部生成。
- 5 个具体游戏全部生成。
- 10 个实体 runtime cache 召回全部命中。
- 日志：`logs/cognition-entities-round2.md`。

### 实体压力第三轮

后置确认：

- 继续使用同等 100 句 transcript 压力输入确认规则稳定。
- 验证已有长期条目默认保留，不因为本轮未直接展开而删除。
- 验证短期污染 key 未出现。
- 验证日志中保留完整对话记录和召回过程，而不是只保留 JSON 汇总。

测试结果：

- 5 个具体人物全部生成。
- 5 个具体游戏全部生成。
- 10 个实体 runtime cache 召回全部命中。
- 日志：`logs/cognition-entities-round3.md`。
