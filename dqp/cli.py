from __future__ import annotations

import argparse
import json
import time

from .engine import sync_and_validate
from .store import Store


def main() -> None:
    ap = argparse.ArgumentParser(prog="dqp", description="Data connection and validation platform")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="load demo sources and run validation")
    r = sub.add_parser("run", help="sync every source and validate")
    r.add_argument("--every", type=int, default=0, help="repeat every N minutes (0 = once)")
    args = ap.parse_args()
    store = Store()
    if args.cmd == "demo":
        from .demo import load_demo
        s = load_demo(store)
        print(json.dumps({k: v for k, v in s.items() if k != "review_candidates"}, indent=2, default=str))
    else:
        while True:
            s = sync_and_validate(store)
            print(f"run {s['run_id']}: {s['issues']} issues {s['by_severity']}")
            if not args.every:
                break
            time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
