# 单角色调度器

实现文件：

- `kokoro/dialogue_orchestrator.py`

## 作用

在单角色场景里判断：

- 现在该不该说
- 应该沉默、短回应还是正常回应
- 是否要用屏幕或网页上下文

## 输出动作

- `silence`
- `backchannel`
- `speak`
- `schedule`
- `observe`
- `cancel_plan`

## 特点

- 先规划，再生成台词
- 角色性格影响节奏，不直接提供话题
- 屏幕/网页缓存只是候选上下文，不是强制材料
