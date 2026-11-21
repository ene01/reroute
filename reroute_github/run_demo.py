"""Terminal demo: reseeds the data, runs every scenario, prints each decision log and a scorecard.

    python run_demo.py              # run all 12 scenarios
    python run_demo.py T003 T007    # run only these
    python run_demo.py --interactive  # also answer the student/staff confirmations yourself
"""

import sys
import time

import agent
import seed_data
import services as svc

COLORS = {"AGENT": "\033[95m", "TOOL": "\033[94m", "GUARD": "\033[93m", "HUMAN": "\033[92m"}
RESET = "\033[0m"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    interactive = "--interactive" in sys.argv
    seed_data.seed()
    with svc.db() as con:
        rows = con.execute("SELECT txn_id, scenario FROM transactions ORDER BY txn_id").fetchall()
    rows = [r for r in rows if not args or r["txn_id"] in args]

    results = []
    for r in rows:
        print(f"\n{'=' * 80}\n{r['txn_id']}  {r['scenario']}\n{'=' * 80}")
        t0 = time.time()
        s = agent.process_transaction(r["txn_id"])
        for e in s["log"]:
            print(f"  {e['step']:>2}. {COLORS.get(e['actor'], '')}{e['actor']:<5}{RESET} "
                  f"{e['event']}: {e['detail']}")
        if s["assessment"]:
            print(f"\n  Student sees: {s['assessment']['student_explanation']}")
        print(f"  Final state: {s['state']}   ({time.time() - t0:.1f}s)")

        if interactive and s["state"] in ("PENDING_CONFIRMATION", "ESCALATED"):
            who = "student" if s["state"] == "PENDING_CONFIRMATION" else "staff"
            ans = input(f"  [{who}] approve this payment? (y/n): ").strip().lower()
            res = agent.resolve(r["txn_id"], approve=ans == "y", actor=who)
            print(f"  -> {res.get('state', res)}")
        results.append(s)

    print(f"\n{'=' * 80}\nSCORECARD\n{'=' * 80}")
    ok = 0
    for s in results:
        match = s["route"] == s["expected_route"]
        ok += match
        print(f"  {s['txn_id']}  expected {s['expected_route']:<22} got {str(s['route']):<22} "
              f"{'OK' if match else 'MISMATCH'}")
    print(f"\n  {ok}/{len(results)} routed as expected")


if __name__ == "__main__":
    main()
