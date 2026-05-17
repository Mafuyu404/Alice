# 屏幕兴趣度

实现文件：

- `kokoro/screen_interest.py`

## 作用

分析当前屏幕是否值得角色主动评论。

## 输出

- `score`
- `content`
- `reason`
- `private`

## 原则

- 登录、支付、聊天、医疗等隐私场景直接降为不可评论
- 调度器只把高分缓存当候选材料，不自动开口
