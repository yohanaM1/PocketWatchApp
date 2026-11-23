"""Verifier catch rate over generated mutations of every known-good answer (proposal target: >= 90%).

Run from backend/ (no database or LLM needed):  python -m scripts.eval_verifier

Writes eval/results/verifier.json and prints the catch rate per kind of mutation and every survivor.
A survivor is either a verifier gap or a mutation that doesn't change the meaning; the write-up has
to say which.
"""

import json
import sys
from collections import Counter
from datetime import datetime

from scripts.eval_common import EVAL_DIR, pct
from tests.agent.mutations import run


def main() -> int:
    """Generate, check, report."""
    muts, survivors = run()
    total, by_kind_total = len(muts), Counter(m.kind for m in muts)
    by_kind_surv = Counter(m.kind for m in survivors)
    caught = total - len(survivors)

    print(f"Verifier catch rate (target >= 90%): {pct(caught, total)}")
    for kind, n in sorted(by_kind_total.items()):
        print(f"  {kind:<10} {pct(n - by_kind_surv[kind], n)}")
    if survivors:
        print("\nSurvivors:")
        for m in survivors:
            print(f"  [{m.case}] {m.kind}: {m.description}")

    out = EVAL_DIR / "results" / "verifier.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "mutations": total, "caught": caught,
        "by_kind": {k: {"mutations": n, "caught": n - by_kind_surv[k]} for k, n in sorted(by_kind_total.items())},
        "survivors": [{"case": m.case, "kind": m.kind, "description": m.description, "text": m.text, "extra": m.extra}
                      for m in survivors],
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
