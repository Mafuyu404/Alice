from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kokoro.core.cognition import CognitionStore

COGNITION_PATH = ROOT / "characters" / "alice" / "cognition.json"
BACKUP_PATH = ROOT / "characters" / "alice" / "cognition.json.testbak"
CASES_PATH = ROOT / "tests" / "cognition_eval_cases.json"


def main() -> int:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    shutil.copy2(COGNITION_PATH, BACKUP_PATH)
    failures: list[str] = []
    try:
        for case in cases:
            shutil.copy2(BACKUP_PATH, COGNITION_PATH)
            store = CognitionStore("alice")
            store.evaluate(
                conversation=case["conversation"],
                summary=case["summary"],
                memories=case["memories"],
                character_name="爱丽丝",
                character_id="alice",
            )
            data = json.loads(COGNITION_PATH.read_text(encoding="utf-8"))
            entries = data.get("entries", {})
            keys = set(entries)
            for key in case.get("must_have_keys", []):
                if key not in keys:
                    failures.append(f"{case['name']}: missing key {key}")
            for term in case.get("forbidden_key_terms", []):
                bad = [key for key in keys if term in key]
                if bad:
                    failures.append(f"{case['name']}: forbidden term {term} in {bad}")
            print(f"[case] {case['name']}: {len(entries)} entries")
            for key in sorted(keys):
                print(f"  - {key}")
    finally:
        shutil.move(BACKUP_PATH, COGNITION_PATH)

    if failures:
        print("\nFAIL")
        for item in failures:
            print(f"- {item}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
