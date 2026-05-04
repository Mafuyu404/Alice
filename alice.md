## 1. 核心概念：冲动与节制的双轨系统

我们让每个主动行为都拥有一个**冲动值**（desire），同时整个系统有一个**干扰容限**（允许打扰的窗口）。两者结合决定“谁”在“何时”可以说话。

- **冲动值** → 回答“我多想现在开口”
- **干扰容限** → 回答“现在开口是否惹人烦”

最终输出 = 冲动值最高且大于全局阈值的那个行为，同时系统干扰指数低于该行为允许的最高干扰级别。

---

## 2. 四种行为的冲动值定义

| 行为代号 | 名称 | 冲动值符号 | 基础累积速度（每秒） | 特殊触发 |
|--------|------|-----------|-------------------|-----------|
| **IDLE** | 闲聊 | \( D_{idle} \) | 0.02 (缓慢) | 系统空闲时速度×3；刚结束一段对话后×2 |
| **RECENT** | 近期对话延伸 | \( D_{recent} \) | 0 (非累积型) | 对话结束30秒后一次性赋值，值=对话质量分(0-100) |
| **MEM** | 长期记忆 | \( D_{mem} \) | 0.01 (极慢) | 特殊日期加成+50；窥屏发现关联内容+40 |
| **SCREEN** | 窥屏话题 | \( D_{screen} \) | 0 (事件触发) | 每次新画面识别出有趣内容+30~70，随后每秒衰减5 |

> 所有冲动值上限 100，触发阈值默认 70（可在设置中调“话多-话少”滑块，阈值范围60~85）。

---

## 3. 干扰容限系统

为保护用户专注状态，给每种行为设定**可打扰等级**，同时系统实时计算干扰指数。

**干扰指数计算（每秒更新一次）**：
```
干扰指数 = 基础值(0) 
  + 应用惩罚(前台全屏应用+50, 视频会议+30, IDE调试模式+20)
  + 活跃度惩罚(键鼠高频操作+20, 游戏手柄输入+30)
  + 隐私惩罚(隐私窗口在前台直接 = 100)
  + 用户状态(勿扰时段+100)
结果钳制在[0,100]
```

**行为可打扰等级**（越低越需要安静时才允许）：
- IDLE闲聊：干扰指数 < 35 时允许
- RECENT对话延伸：干扰指数 < 45 （此事宜趁热）
- MEM记忆：干扰指数 < 25 （需要氛围）
- SCREEN窥屏：干扰指数 < 50 （画面可能随时变化，可稍微容忍）

这些阈值不是死的，会随用户反馈微调。

---

## 4. 决策调度算法

系统每 **20秒** 执行一次决策 tick，但用户主动呼叫可以**无条件打断**任何流程，并重置所有冲动值。

### 4.1 冲动值更新规则

```python
def update_desires(dt):
    # dt 为距上次tick的秒数
    
    # 闲聊冲动：基础累积
    D_idle += base_rate_idle * dt
    if user_idle_minutes > 2:          # 系统监测到用户发呆
        D_idle += 3 * base_rate_idle * dt
    if seconds_since_last_conversation < 120:
        D_idle += 2 * base_rate_idle * dt   # 刚聊完更容易继续拉家常
    D_idle = min(D_idle, 100)

    # 近期对话延伸冲动：对话结束后一次性注入，之后随时间衰减
    if new_conversation_ended:
        D_recent = quality_of_last_conversation()  # 0-100，依据对话长度、情绪等
        decay_start = time.now()
    else:
        if D_recent > 0:
            elapsed = now - decay_start
            D_recent = max(0, D_recent - elapsed * 2)   # 2点/秒衰减，约50秒归零

    # 长期记忆冲动：缓慢累积 + 日期/事件冲击
    D_mem += base_rate_mem * dt
    if is_special_date_today():       # 用户生日、纪念日等
        D_mem = min(100, D_mem + 50)
    if screen_content_triggers_memory():  # 窥屏发现老照片等
        D_mem = min(100, D_mem + 40)
    D_mem = min(D_mem, 100)

    # 窥屏冲动：由画面事件赋予，快速衰减
    if screen_analysis_finished:
        interest_score = analyze_interest_in_screen()  # 返回 0-100
        if interest_score > 50:
            D_screen = interest_score
    D_screen = max(0, D_screen - dt * 5)  # 每秒衰减5，20秒冲动就会掉落100，仅瞬间机会
```

### 4.2 决策逻辑（每个tick）

```python
def schedule_active_speech():
    # 1. 先更新冲动
    update_desires(time.delta)

    # 2. 计算全局干扰
    disturb = calculate_disturbance_index()

    # 3. 构建候选行为列表
    candidates = []
    for behavior, desire in [
        ('IDLE', D_idle), ('RECENT', D_recent), 
        ('MEM', D_mem), ('SCREEN', D_screen)
    ]:
        if desire >= active_threshold:      # 达到基础冲动
            if disturb <= allowed_disturbance[behavior]:  # 干扰不超标
                candidates.append((behavior, desire))
    
    if not candidates:
        return  # 安静

    # 4. 如果有多个候选，用两点策略挑选
    #    优先考虑“时效性强”和“避免重复”
    final_choice = select_with_diversity(candidates, history)

    # 5. 执行搭话
    execute_behavior(final_choice)
    
    # 6. 重置该行为的冲动值，并记录冷却时间
    reset_desire(final_choice)
    add_cooldown(final_choice, 90 seconds)  # 同种行为最小间隔
```

**挑选多样性算法**：
```python
def select_with_diversity(candidates, history):
    # 去除最近2分钟内已执行过的行为
    candidates = [c for c in candidates if c[0] not in recent_behaviors(120)]

    if not candidates:
        return None

    # 优先选择冲动值最高的，但加入20%概率随机选择次高，避免死板
    sorted_cands = sorted(candidates, key=lambda x: x[1], reverse=True)
    if random.random() < 0.2 and len(sorted_cands) > 1:
        return sorted_cands[1][0]  # 偶尔给次高机会
    return sorted_cands[0][0]
```

---

## 5. 用户反馈闭环：学会“知趣”

桌面宠物需要听懂“闭嘴”“说点别的”等反馈，逐步调整个性。

在每次主动搭话后，监听接下来10秒内的用户反应：

| 用户反应 | 系统解读 | 调整动作 |
|---------|---------|---------|
| 语音回应“哈哈”“继续”/点击宠物 | **积极反馈** | 该行为权重+5%，全局阈值略微降低 |
| 无反应，继续当前工作 | **中性** | 无变化 |
| 说“别吵”“安静”/叉掉气泡 | **消极反馈** | 该行为权重-20%，全局阈值临时提高15，持续10分钟 |
| 用户立即开启新应用/开始打字 | **打断信号** | 同消极，且当前行为冷却加倍 |

权重将改变该行为的**基础累积速度**（通过系数 \( w_{behavior} \) 默认1.0，范围0.2~2.0）。长期下来，AI会更贴合你的偏好——如果你从不回应记忆搭话，它就会少回忆；如果你常接窥屏梗，它会更多评论你的屏幕。

---

## 6. 完整时间线模拟

假设你下午刚打开电脑：

- **14:00** 系统启动，所有冲动值0，干扰0。  
- **14:05** 你打开浏览器刷社交网络。干扰指数20。  
  - \( D_{idle} \) 累积到 12，未达阈值。  
  - 屏幕分析发现萌宠图片，\( D_{screen} = 65 \)，未达70阈值，但已接近。
- **14:10** 你盯着同一张猫图停留10秒。画面未刷新，窥屏不会重复触发，但IDLE冲动因静止快速累积（空闲加成），达到35。  
  - 仍无候选。  
- **14:15** 你切换网页时，屏幕分析到一张有趣的梗图，兴趣分85，\( D_{screen}=85 \)，干扰指数25。  
  - 决策 tick 触发：候选 SCREEN(85) 通过，宠物：*“这表情包是你本人吧哈哈”*  
  - \( D_{screen} \) 重置，冷却90秒。
- **14:17** 你大笑回应“哈哈像吗”。系统识别积极反馈，增加SCREEN权重。对话结束，评价质量分80，\( D_{recent}=80 \)，干扰指数30。  
  - 下一个 tick 候选 RECENT(80)，允许干扰等级45通过，宠物：*“其实你上次聊天也发过一个类似猫的，果然猫系。”* （结合近期对话）
- **14:30** 你开始写文档，干扰指数升到60。此时 \( D_{idle} \) 已累积到71，但IDLE允许干扰<35，被过滤，所以它不会开口。你享受到专注。
- **15:30** 你暂停工作，走到窗边。监测到空闲5分钟，干扰降到15，\( D_{idle}=92 \) 触发，宠物：*“坐久了，我陪你伸个懒腰？”*

---

## 7. 实现路线图建议

1. **先跑调度器框架**：仅用IDLE和RECENT，用简单的规则累积和干扰判断，让宠物的“主动安静”功能可靠。
2. **加入屏幕事件钩子**：实现SCREEN的瞬时冲动与衰减，重点测试隐私暂停和错误的识图抑制。
3. **注入记忆与日期**：MEM的累积慢，初期只需显示生日提醒，确保数据库健壮。
4. **上反馈学习**：通过用户对搭话的回应来调整权重。这一步会让宠物越养越贴心。
5. **可视化调试面板**：为开发阶段做一个实时仪表盘，显示四个冲动值柱状图、干扰指数和候选列表，直观调参。

---

这套设计把随意搭话变成了 **“被环境允许 + 内心有冲劲 + 内容应景”** 的三重过滤行为。最终，你的桌面宠物会表现得像一只善解人意的猫：它知道什么时候该跳上键盘打扰你，什么时候只需在角落安静地陪着你。