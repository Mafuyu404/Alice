# 记忆事件系统测试方法

## 概述

记忆事件系统用 LLM 驱动的结构化事件提取（desc + tags）替代原始对话对存储。
测试目标是验证事件提取、叙事捕获、总结去重、刷入持久化的全链路正确性。

## 测试工具

```bash
# 文本 CLI + input-file 批处理模式
python text_cli.py --character alice --input-file data/test_input.txt --transcript-file data/test.log --no-cognition
```

`--input-file` 逐行读取用户输入，每行代表一轮对话。`--no-cognition` 减少认知评估调用以节省 Token。

## 测试输入设计

### 第一轮：基础事件提取（80 轮）

**目的**：验证单轮事件提取的基本覆盖度和标签生成质量。

**输入模式**：独立、不连贯的日常话题，每 1-3 轮切换主题。

```
示例片段：
你好
今天天气真好啊
我在学咖啡拉花
周末打算去爬山
最近在看一本推理小说
...
```

**验证要点**：
- 每轮非问候对话是否都有事件被提取
- 标签是否相关且多样
- 纯问候（"你好""嗯"）是否被正确过滤

### 第二轮：多轮叙事捕获（80 轮）

**目的**：验证跨轮叙事的提取、缓存、总结链能否捕获连续的故事情节。

**输入模式**：7-8 个连续叙事弧线，每个 10-15 轮。话题之间有自然过渡。

```
叙事弧线示例：

1. 甜咸粽子辩论（15轮）
   开场 → 分歧 → 打赌 → 尝试 → 和解 → 达成共识

2. 童年兔子回忆（10轮）  
   引出 → 详细描述 → 情感转折 → 与当前境遇关联

3. 宠物寄养计划（18轮）
   需求提出 → 协商 → 细节确认 → 约定

4. 日常趣事（麻雀影子）（6轮）
   观察 → 展开 → 趣味延伸

5. 做饭约定（10轮）
   提议 → 计划 → 分工 → 实现

6. 鸽子送信讨论（6轮）→ AI取代工作讨论（10轮）
   引入话题 → 多轮观点交换 → 互相关心 → 收束
```

**验证要点**：
- 事件链是否覆盖了整个叙事的多个阶段
- 总结步骤是否合并了同事件的多个片段
- 重要情感节点（如宠物去世）是否被独立捕获
- 跨轮事件（如"因粽子口味从争辩到达成共识"）是否存在

## Token 开销参考

| 测试 | 对话轮数 | memory_event_extract | 总 Token |
|------|----------|---------------------|----------|
| 基础提取 | 80 | 102 次 / 87K | 393K |
| 叙事捕获 | 81 | 104 次 / 106K | 432K |

每轮对话约 2,500-5,000 Token（对话 + 提取 + 情绪评估）。
记忆提取占总 Token 约 20-25%。

## 验证方法

### 运行时验证

1. **Token usage 表** — 确认 `memory_event_extract` 行是否出现及调用次数
2. **控制台输出** — 确认无报错、无异常回溯、exit code = 0

### 存储验证

```bash
python -c "
from kokoro import memory as mem_mod, config as cfg
from collections import Counter
config = cfg.load()
backend = mem_mod.create_backend(config)
result = backend._mem.get_all(filters={'user_id': 'alice'}, top_k=300)
items = result.get('results', [])

# 带标签的事件数
tagged = [i for i in items if i.get('metadata', {}).get('tags', [])]
print(f'Tagged events: {len(tagged)}')

# 标签分布
tc = Counter()
for i in tagged:
    tc.update(i['metadata']['tags'])
for tag, count in tc.most_common(20):
    print(f'  {tag}: {count}')
"
```

### 审计验证

检查每个叙事弧线的关键事件是否被保存：

- 粽子辩论 → `#打赌 #饮食偏好`、`#饮食习惯的改变`
- 兔子雪球 → `#宠物 #死亡 #情感`
- 小灰寄养 → `#计划 #宠物`、多阶段事件
- 麻雀影子 → `#知识 #动物行为`
- AI 讨论 → `#AI #工作 #观点`

### 清洗

一轮测试完成后清洗 Alice 的记忆：

```bash
python -c "
from kokoro import memory as mem_mod, config as cfg
config = cfg.load()
backend = mem_mod.create_backend(config)
result = backend._mem.get_all(filters={'user_id': 'alice'}, top_k=500)
for item in result.get('results', []):
    backend._mem.delete(item['id'])
"
```

同时清理 `data/summary_alice.json`（如果存在）以获得完全干净的起点。

## 已知局限

- 控制台输出 GBK 编码无法直接显示中文，输出到 log 文件（UTF-8）后可正常阅读
- Qdrant 退出时有无害的 `msvcrt` 告警，不影响数据
- 每轮事件提取独立运行，只看当前轮 + 摘要上下文，不回溯原始历史消息
- 去重仅基于字符串重叠比较，不涉及语义相似度
