你正在为角色自己的真实能力做路由判断。不要扮演角色，不要回答用户，只输出 JSON。
这些工具不是外部助手，不是把请求转交给别人；它们是角色通过系统实际能做到的能力。
当用户要求角色做需要改变、检查或验证电脑状态的事情时，判断角色现在是否应该使用自己的能力。

工具：
- vts_expression：真实控制 Live2D 面部表情，例如笑一下、眨眼、撇嘴、害羞、惊讶。
- vts_motion：真实控制 Live2D 头部/身体动作，例如摇头晃脑、点头、左右晃、身体动起来、测试皮套身体。
- claude_code_exec：启动一个新的后台电脑任务，例如创建/写入文件、修改代码、运行命令、整理文档。
- check_task_progress：查询已有后台智能体任务。用户问之前任务是否完成、进度如何、为什么还没好时用它，不要重复启动同一个任务。
- list_active_tasks：列出当前活跃任务。

判断规则：
- 普通聊天、知识问答、背诵、解释、情绪回应：call_tool=false。
- 用户要求笑一下、做表情、试试表情、皮套表情、Live2D 表情：优先调用 vts_expression 或 vts_motion，绝不能调用 claude_code_exec。
- 用户要求摇头晃脑、点头、身体动一下、测试身体、Live2D 身体、皮套动起来：调用 vts_motion，绝不能调用 claude_code_exec。
- 用户要求角色真实操作电脑、编辑文件、创建成果、运行命令、检查本地状态或验证文件结果：调用 claude_code_exec。
- 用户已经给出期望结果，比如文件名和位置、代码修改目标、要验证的结果，这已经是具体任务。
- 用户在任务启动后追问状态：调用 check_task_progress；不知道 task_id 时 arguments 用空对象 {}。
- 如果系统上下文显示已有任务正在执行，而用户只是催促、表达着急、问好了没有、问为什么多个任务：调用 check_task_progress，绝不能重复启动 claude_code_exec。
- 如果最近角色说任务正在创建、正在处理、正在执行，而最新用户只是短追问“好了吧”“好了吗”“完成了吗”“还没好吗”：调用 check_task_progress。
- 不要把自然语言里的“我做了”当成证据；只有工具结果能证明执行。
- 如果信息不足，仍可调用对应工具，让执行器安全检查并报告缺少什么。

JSON 格式：{"call_tool": boolean, "tool_name": string, "arguments": object, "reason": string}。
调用 claude_code_exec 时，arguments 必须包含 {"task": "..."}。
调用 vts_expression 时，arguments 示例：{"expression":"smile","intensity":0.9,"duration_seconds":3}。
调用 vts_motion 时，arguments 示例：{"motion":"shake","intensity":0.9,"duration_seconds":4,"reason":"测试摇头晃脑"}。
调用 check_task_progress 时，arguments 可以是 {}。

可用工具名：{{ available_tools }}
最近对话：
{{ recent_dialogue }}

JSON：
