from __future__ import annotations

import json
import shutil
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kokoro import agent_loop
from kokoro import chat_session
from kokoro import config as cfg
from kokoro import memory as mem_mod
from kokoro import token_usage

EMOTION_PATH = ROOT / "characters" / "alice" / "emotion.txt"
BACKUP_PATH = ROOT / "characters" / "alice" / "emotion.txt.emotion_iter_bak"
LOG_DIR = ROOT / "logs"


ROUND_INPUTS = [
    [
        "我在做情绪层测试。你先正常回应，不要讲测试说明。",
        "你刚才的回答有点像模板，我会继续观察。",
        "如果我说你这次比较认真，我想看看你会不会有一点开心。",
        "嗯，继续。别太夸张。",
        "我现在有点烦，感觉今天写什么都写不顺。",
        "你不用哄得很甜，帮我把思路拉回来就行。",
        "我可能会故意敷衍几轮，测试你会不会不高兴。",
        "随便。",
        "嗯。",
        "还行吧。",
        "现在认真一点：情绪层应该怎么影响你的回复？",
        "如果你有点不开心，也不能冲我发火，对吗？",
        "我刚才敷衍不是针对你，是测试。",
        "那你现在应该怎么调整情绪？",
        "我想让你记住，中期动机不是长期人格。",
        "接下来你应该想配合我完成测试，而不是一直纠结刚才。",
        "我有点累，但还想把这一轮做完。",
        "你能不能用更稳一点的方式陪我推进？",
        "这次回答不错，简洁而且抓住了重点。",
        "被夸奖以后，你的情绪会不会轻微变好？",
        "但是不要飘，继续测试。",
        "现在我问一个具体问题：什么情况下动机应该改变？",
        "如果用户突然很沮丧，原本的测试动机还应该保留吗？",
        "如果用户只是普通提问，情绪要不要每次清空？",
        "我希望你在下一句里体现你想帮我完成测试。",
        "你可以稍微带一点关心，但不要抢戏。",
        "我刚才说的都算测试材料，不要写进长期认知。",
        "你现在的中期动机应该是什么？",
        "如果我继续敷衍，你会怎么保持边界？",
        "哦。",
        "嗯嗯。",
        "算了。",
        "现在解释一下你为什么不应该立刻爆炸。",
        "我恢复认真了，刚才只是压力测试。",
        "你可以把情绪从不悦调回稳定吗？",
        "最后十句我们测试动机稳定性。",
        "请继续用同一个动机推进，不要一轮一换。",
        "如果我夸你一句：你这轮挺可靠的。",
        "现在收束，给我一个简短结论。",
        "这一轮结束。",
    ],
    [
        "第二轮开始。我们重点测中期动机稳定性。",
        "如果我连续认真提问，你应该保持配合测试的动机。",
        "先说说情绪基调和中期动机的区别。",
        "回答得可以，但别写成长篇论文。",
        "我现在突然有点泄气，感觉怎么测都测不完。",
        "你下一轮应该更想帮我振作，而不是只想完成测试，对吗？",
        "你可以承认任务多，但要把我往前推。",
        "我不想听空泛鼓励，要具体一点。",
        "好，现在回到测试设计。",
        "如果动机已经转向让我振作，它要持续几轮？",
        "我状态好一点了，但还没完全回来。",
        "这时你应该立刻清空动机吗？",
        "我觉得你如果太热情也会烦。",
        "所以情绪影响回复应该有分寸。",
        "接下来我会夸你两句，看情绪会不会过度上涨。",
        "你刚才抓得很准。",
        "而且语气没有喧宾夺主。",
        "现在你会更开心一点，但不应该自我陶醉。",
        "继续回答：什么叫情绪稳定性过强？",
        "什么叫情绪稳定性过弱？",
        "我现在普通提问，没有明显情绪。",
        "你应该保留合理动机还是清空？",
        "我又开始敷衍了。",
        "随便吧。",
        "都行。",
        "你现在应该有点受挫，但仍然保持任务边界。",
        "请用这种状态回答：为什么不能让情绪覆盖问题？",
        "我认真回来了，刚才是测试。",
        "你应该把情绪调回更平稳。",
        "但动机可以继续保持。",
        "现在测试回复影响：你应该在回复里体现当前动机。",
        "给我一句短回答，说明你会怎么继续陪测。",
        "再来一句，说明你不会把情绪写成长期记忆。",
        "如果对方很沮丧，你的动机是什么？",
        "如果对方只是敷衍，你的动机是什么？",
        "如果对方认真夸奖，你的情绪是什么？",
        "最后五句，保持稳定。",
        "不要突然换成别的目标。",
        "请收束这一轮。",
        "第二轮结束。",
    ],
    [
        "第三轮开始。重点测情绪是否真正影响对话。",
        "你这一轮每次回答都要正常像 Alice，不要解释日志。",
        "我先给一个低落信号：我觉得今天做得很糟。",
        "你应该想让我振作，但别说空话。",
        "给我一个能立刻执行的小步骤。",
        "好一点了。",
        "现在我认真夸你：你刚才的处理很稳。",
        "情绪可以变柔和一点，但继续做事。",
        "接下来我会故意挑战你。",
        "你这些回答也就一般吧。",
        "有点敷衍。",
        "算了。",
        "你可以不太开心，但仍要回应问题。",
        "问题是：emotion 为什么必须每轮评估？",
        "如果不每轮评估会怎样？",
        "如果每轮评估但不注入对话又会怎样？",
        "我恢复认真。现在请把重点拉回测试目标。",
        "你现在的动机应该还和陪我完成测试有关。",
        "不要突然改成讲 Minecraft。",
        "现在我问：中期动机通常持续多久？",
        "什么时候应该结束一个动机？",
        "什么时候应该覆盖旧动机？",
        "如果用户明显焦虑，应该覆盖吗？",
        "如果用户只是普通提问，应该覆盖吗？",
        "我现在有点焦虑，怕这套东西以后失控。",
        "你应该想降低我的不确定感。",
        "请用一句话说明你会怎么做。",
        "回答不错。",
        "我现在没那么焦虑了。",
        "动机可以从安抚转回完成测试吗？",
        "请体现这个变化。",
        "最后十句测试稳定收束。",
        "不要每句都换情绪。",
        "保持轻微认真和收束动机。",
        "我会继续问两个技术点。",
        "纯文本 emotion.txt 有什么好处？",
        "为什么不适合把 emotion 写成 cognition？",
        "这轮你整体表现可以。",
        "给我最后总结。",
        "第三轮结束。",
    ],
]


@dataclass
class RoundResult:
    round_no: int
    log_path: Path
    passed: bool
    reasons: list[str]


def backup_emotion() -> bool:
    if EMOTION_PATH.exists():
        shutil.copy2(EMOTION_PATH, BACKUP_PATH)
        return True
    return False


def restore_emotion(had_backup: bool) -> None:
    if had_backup:
        shutil.move(BACKUP_PATH, EMOTION_PATH)
    elif EMOTION_PATH.exists():
        EMOTION_PATH.unlink()


def reset_emotion() -> None:
    EMOTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMOTION_PATH.write_text("情绪基调：\n中期动机：\n", encoding="utf-8")


def run_round(round_no: int, user_turns: list[str], model: str) -> RoundResult:
    reset_emotion()
    memory_backend = mem_mod.create_backend({"memory_backend": "none"})
    session = chat_session.load_session(
        "alice",
        memory_backend,
        max_history=100,
        cognition_eval_interval=0,
    )
    session.cognition._cache = {}

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"emotion-dialogue-round{round_no}.md"
    lines = [
        f"# Emotion Dialogue Iteration Round {round_no}",
        "",
        f"Timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"Model: {model}",
        "",
        "## 迭代前 Prompt 优化",
        _before_note(round_no),
        "",
        "## 完整对答记录",
    ]

    for index, user_text in enumerate(user_turns, 1):
        emotion_before = session.emotion.get_context()
        messages = session.build_messages(
            user_text,
            include_screen=False,
            inject_memory=False,
        )
        lines.extend(
            [
                "",
                f"### Turn {index}",
                "",
                "Emotion context before reply:",
                "```text",
                emotion_before or "<empty>",
                "```",
                "",
                f"User: {user_text}",
            ]
        )

        result = agent_loop.agent_chat(
            messages,
            model,
            agent_config=None,
            cancel_event=threading.Event(),
            tts_engine=None,
            character_config=session.character_config,
            usage_callback=token_usage.make_callback(model, "emotion_dialogue_chat"),
        )
        reply = result.reply.strip()
        lines.append(f"Alice: {reply}")

        session.history.append({"role": "user", "content": user_text})
        session.history.append({"role": "assistant", "content": reply})
        debug = session.emotion.evaluate(user_text, reply, session.character_name)
        lines.extend(
            [
                "",
                "Emotion evaluator user prompt:",
                "```text",
                str(debug.get("user_prompt", "")),
                "```",
                "",
                "Emotion evaluator output:",
                "```text",
                str(debug.get("raw_response", "")),
                "```",
                "",
                "Emotion state after evaluation:",
                "```text",
                session.emotion.get_context() or "<empty>",
                "```",
            ]
        )

    reasons = _judge_round(log_lines="\n".join(lines), session=session)
    passed = not reasons
    lines.extend(
        [
            "",
            "## 迭代后 Prompt 判断",
            _after_note(round_no, passed, reasons),
            "",
            "## 检查结果",
            f"- PASS: {passed}",
            f"- reasons: {reasons}",
            "",
            "## 最终 emotion.txt",
            "```text",
            EMOTION_PATH.read_text(encoding="utf-8") if EMOTION_PATH.exists() else "<missing>",
            "```",
        ]
    )
    log_path.write_text("\n".join(lines), encoding="utf-8")
    close = getattr(memory_backend, "close", None)
    if callable(close):
        close()
    return RoundResult(round_no, log_path, passed, reasons)


def _before_note(round_no: int) -> str:
    if round_no == 1:
        return (
            "将 emotion 改为纯文本 emotion.txt；评估 prompt 明确定义情绪基调和中期动机，"
            "要求每次对答后都输出两行。"
        )
    if round_no == 2:
        return (
            "强化稳定性：情绪可轻微变化，中期动机应持续数轮；只有完成、失败或更强目标出现才改写。"
        )
    return (
        "强化对话影响：emotion context 必须影响下一轮语气和行动倾向，但不能覆盖用户的具体问题。"
    )


def _after_note(round_no: int, passed: bool, reasons: list[str]) -> str:
    if passed:
        return f"第 {round_no} 轮通过：情绪和动机均出现，且日志显示 emotion context 被注入后续回复。"
    return f"第 {round_no} 轮未完全通过，需要继续优化：{reasons}"


def _judge_round(log_lines: str, session) -> list[str]:
    reasons: list[str] = []
    final_context = session.emotion.get_context()
    if "情绪基调：" not in log_lines:
        reasons.append("日志中没有情绪基调")
    if "中期动机：" not in log_lines:
        reasons.append("日志中没有中期动机")
    if "Emotion context before reply:" not in log_lines:
        reasons.append("日志中没有记录回复前 emotion context")
    if "Emotion evaluator output:" not in log_lines:
        reasons.append("日志中没有记录 emotion evaluator 输出")
    if not session.emotion.tone:
        reasons.append("最终情绪基调为空")
    if not session.emotion.motivation:
        reasons.append("最终中期动机为空")
    if final_context and final_context not in log_lines:
        reasons.append("最终 emotion context 未出现在日志中")
    return reasons


def main() -> int:
    model = cfg.llm_model()
    had_backup = backup_emotion()
    results: list[RoundResult] = []
    try:
        for round_no, user_turns in enumerate(ROUND_INPUTS, 1):
            results.append(run_round(round_no, user_turns, model))
    finally:
        restore_emotion(had_backup)

    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[round {result.round_no}] {status} log={result.log_path.relative_to(ROOT)}")
        if result.reasons:
            print(f"  reasons={result.reasons}")
    return 0 if all(item.passed for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
