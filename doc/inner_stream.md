# 内在叙事流

内在叙事流是角色的自我表达连续性层。

实现文件：

- `kokoro/inner_stream.py`

运行文件：

- `characters/{character_id}/inner_stream.txt`

## 作用

它维护一段自然语言文本，描述角色当前残留的想法、表达倾向、话题耐心、想继续推进或暂时不想展开的方向。

它不是：

- 兴趣权重表
- 计划表
- 程序可解析规则
- 长期人格设定
- 事件记忆

## 与现有层的关系

- memory：记录具体事件
- cognition：记录稳定印象
- emotion：保留给表情/VTS 等外部兼容路径
- inner stream：吸收当前情绪、短期动机、表达冲动和话题耐心，形成“我现在作为我还在想什么”
- dialogue orchestrator：读取 inner stream，但仍由 LLM 自主判断是否开口、是否转向、是否沉默

## 更新时机

每轮对话写入后，`ChatSession.remember()` 会同步更新 inner stream，确保下一轮调度能立即看到最新内在状态。

主动对话的 scheduled 发言也会进入同一链路，避免角色主动说过的话脱离自身连续性。

## 设计原则

- 程序只负责读写和注入文本，不根据文本内容做 if/else。
- LLM 负责重写内在叙事流。
- 内在叙事流可以影响对话调度，但不是命令。
- 如果没有明显变化，可以保留旧流并轻微修正。

## 配置

```toml
[inner_stream]
enabled = true
model = ""
max_chars = 1200
max_tokens = 700
```
