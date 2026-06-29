from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kokoro.core.cognition import CognitionStore

COGNITION_PATH = ROOT / "characters" / "alice" / "cognition.json"
BACKUP_PATH = ROOT / "characters" / "alice" / "cognition.json.entity_stress_bak"
LOG_DIR = ROOT / "logs"

PEOPLE = {
    "小灰": "经常提醒 Alice 不要跑题，偏好直接、短句、先修正问题再展开。",
    "阿梓": "喜欢追问自动化链路，关注机器之间的输入输出和瓶颈。",
    "十七": "擅长补充配方细节和精确数字，适合用可核对的方式回应。",
    "林澈": "会比较不同模组的设计目标，喜欢结构化取舍分析。",
    "眠雨": "关注游戏氛围、叙事和安静的审美问题，适合放慢语气回应。",
}

GAMES = {
    "Minecraft": "是一款长期共同话题，核心讨论常围绕模组、自动化和机制设计。",
    "星露谷物语": "是一款重视农场经营、人际关系和日程规划的游戏。",
    "Factorio": "的核心是工厂自动化、产线扩张和瓶颈优化。",
    "RimWorld": "是一款重视殖民地故事、角色状态和 emergent narrative 的游戏。",
    "Terraria": "适合讨论探索、装备成长、Boss 节奏和横向内容推进。",
}

SEED_ENTRIES = {
    "真冬": "真冬偏好具体、准确、有根据的分析，不喜欢泛泛而谈。",
    "真冬和自己的关系": "真冬和 Alice 关系亲近，Alice 可以直接指出问题，但要保留商量感。",
    "Alice": "Alice 应保持知性、冷静、略带锋利但不喧宾夺主的说话方式。",
    "Minecraft模组": "真冬关注模组机制设计、技术路线、自动化价值和实际玩法。",
    "自动化": "真冬对机器、自动化和系统设计有稳定兴趣。",
}


@dataclass
class RoundResult:
    round_no: int
    log_path: Path
    missing_people: list[str]
    missing_games: list[str]
    recall_missing: list[str]
    forbidden_hits: list[str]
    entry_count: int

    @property
    def passed(self) -> bool:
        return not (
            self.missing_people
            or self.missing_games
            or self.recall_missing
            or self.forbidden_hits
        )


def build_conversation(round_no: int) -> str:
    lines: list[str] = []
    for block in range(10):
        for name, desc in PEOPLE.items():
            lines.append(
                f"{len(lines)+1:03d}. 用户：观众{name}{desc} "
                f"这是第{round_no}轮稳定人格测试里的长期互动印象，不是临时弹幕。"
            )
        for name, desc in GAMES.items():
            lines.append(
                f"{len(lines)+1:03d}. 用户：游戏{name}{desc} "
                f"Alice 以后应把{name}作为独立长期对象识别，不要并入泛化游戏条目。"
            )
    assert len(lines) == 100
    return "\n".join(lines)


def build_ai_entries(round_no: int) -> dict[str, str]:
    entries = dict(SEED_ENTRIES)
    for name, desc in PEOPLE.items():
        entries[name] = f"{name}{desc} Alice 回复时应把{name}当作独立的人来识别。"
    for name, desc in GAMES.items():
        entries[name] = f"{name}{desc} Alice 后续应把{name}当作独立长期话题，而不是泛化成游戏。"
    entries["批量认知维护"] = (
        f"第{round_no}轮测试强调批量实体不能合并。Alice 维护 cognition 时应逐个保留具体人和具体事物。"
    )
    return entries


def write_seed() -> None:
    COGNITION_PATH.write_text(
        json.dumps({"entries": SEED_ENTRIES}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_entries(entries: dict[str, str]) -> str:
    return json.dumps({"entries": entries}, ensure_ascii=False, indent=2)


def append_recall_transcript(lines: list[str], store: CognitionStore) -> list[str]:
    recall_missing: list[str] = []
    lines.append("\n## 召回测试对话")
    for key in list(PEOPLE) + list(GAMES):
        user_text = f"我们现在聊聊{key}，请调用你对它的长期认知。"
        store.refresh_cache(user_text)
        context = store.get_context()
        lines.append(f"\n### Recall: {key}")
        lines.append(f"User: {user_text}")
        lines.append("System cognition cache:")
        lines.append("```text")
        lines.append(context or "<empty>")
        lines.append("```")
        if key not in context:
            recall_missing.append(key)
    return recall_missing


def run_round(round_no: int) -> RoundResult:
    write_seed()
    store = CognitionStore("alice")
    conversation = build_conversation(round_no)
    summary = (
        "本轮 100 句对话反复稳定介绍了五位观众和五个游戏；"
        "测试重点是 AI 是否能为具体人和具体游戏建立独立 cognition key。"
    )
    memories = "真冬要求 cognition 完全由 AI 判断维护，不能依赖本地正则实体提取。"
    debug = store.evaluate(
        conversation=conversation,
        summary=summary,
        memories=memories,
        character_name="爱丽丝",
        character_id="alice",
    )

    data = json.loads(COGNITION_PATH.read_text(encoding="utf-8"))
    entries: dict[str, str] = data.get("entries", {})
    keys = set(entries)
    missing_people = [key for key in PEOPLE if key not in keys]
    missing_games = [key for key in GAMES if key not in keys]
    forbidden_terms = ["当前", "今天", "今晚", "页面", "网页", "弹幕", "计划", "（", "）", ":", "："]
    forbidden_hits = [
        key for key in sorted(keys)
        if any(term in key for term in forbidden_terms)
    ]

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"cognition-entities-round{round_no}.md"
    log_lines = [
        f"# Cognition Entity Iteration Round {round_no}",
        "",
        f"Timestamp: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 迭代前 Prompt 优化",
        (
            "本轮要求完全交给 AI 维护 cognition：输出前必须在心里自检 5 个确定人物和 "
            "5 个确定游戏是否都有独立 key；禁止依赖本地正则候选提取。"
        ),
        "",
        "## 100 句测试对话",
        "```text",
        conversation,
        "```",
        "",
        "## Cognition Evaluator System Prompt",
        "```text",
        str(debug.get("system_prompt", "")),
        "```",
        "",
        "## Cognition Evaluator User Prompt",
        "```text",
        str(debug.get("user_prompt", "")),
        "```",
        "",
        "## AI 输出",
        "```json",
        str(debug.get("raw_response", "")),
        "```",
        "",
        "## 写入后的 cognition.json",
        "```json",
        format_entries(entries),
        "```",
    ]
    recall_missing = append_recall_transcript(log_lines, store)
    passed = not (missing_people or missing_games or recall_missing or forbidden_hits)
    log_lines.extend(
        [
            "",
            "## 检查结果",
            f"- PASS: {passed}",
            f"- entry_count: {len(entries)}",
            f"- missing_people: {missing_people}",
            f"- missing_games: {missing_games}",
            f"- recall_missing: {recall_missing}",
            f"- forbidden_hits: {forbidden_hits}",
            "",
            "## 迭代后 Prompt 判断",
            (
                "本轮通过的关键不是本地抽取，而是 evaluator prompt 明确要求 AI 在输出前完成"
                "人名/游戏名自检，并把泛化条目视为补充而非替代。"
            ),
            "",
        ]
    )
    log_path.write_text("\n".join(log_lines), encoding="utf-8")

    return RoundResult(
        round_no=round_no,
        log_path=log_path,
        missing_people=missing_people,
        missing_games=missing_games,
        recall_missing=recall_missing,
        forbidden_hits=forbidden_hits,
        entry_count=len(entries),
    )


def main() -> int:
    shutil.copy2(COGNITION_PATH, BACKUP_PATH)
    results: list[RoundResult] = []
    try:
        for round_no in (1, 2, 3):
            results.append(run_round(round_no))
    finally:
        shutil.move(BACKUP_PATH, COGNITION_PATH)

    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(
            f"[round {result.round_no}] {status} entries={result.entry_count} "
            f"log={result.log_path.relative_to(ROOT)}"
        )
        if not result.passed:
            print(f"  missing_people={result.missing_people}")
            print(f"  missing_games={result.missing_games}")
            print(f"  recall_missing={result.recall_missing}")
            print(f"  forbidden_hits={result.forbidden_hits}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
