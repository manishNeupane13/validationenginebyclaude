"""Human-in-the-loop queue. The model suggests; a person decides; decisions are remembered and change future runs."""
from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from ..canon import build_all
from ..store import Store, now
from .client import LLMClient
from .tasks import judge_match, review_issue, write_report

ISSUE_RULES = ("mismatch", "spelling", "format", "invalid_value", "possible_duplicate")


def pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


class ReviewQueue:
    def __init__(self, store: Store):
        self.s = store

    # ---- filling the queue --------------------------------------------------------------
    def enqueue_matches(self, candidates: list[dict]) -> int:
        added = 0
        with self.s.conn() as c:
            decided = {(r["a"], r["b"]) for r in c.execute("SELECT a, b FROM match_decisions")}
            for cand in candidates:
                a, b = pair_key(cand["a"], cand["b"])
                if (a, b) in decided:
                    continue
                cur = c.execute("INSERT OR IGNORE INTO reviews(kind,entity,subject,payload,created_at) VALUES('match',?,?,?,?)",
                                (cand["entity"], f"match:{a}|{b}", json.dumps({"a": a, "b": b, "score": cand["score"]}), now()))
                added += cur.rowcount
        return added

    def enqueue_issues(self, rules: tuple[str, ...] = ISSUE_RULES, limit: int = 200) -> int:
        df = self.s.issues()
        df = df[(df.status == "open") & df.rule.isin(rules)].head(limit)
        added = 0
        with self.s.conn() as c:
            for r in df.itertuples():
                cur = c.execute("INSERT OR IGNORE INTO reviews(kind,entity,subject,payload,created_at) VALUES('issue',?,?,?,?)",
                                (r.entity, f"issue:{r.fingerprint}", json.dumps({"fingerprint": r.fingerprint}), now()))
                added += cur.rowcount
        return added

    # ---- reading ---------------------------------------------------------------------------
    def items(self, kind: str | None = None, status: str | None = None) -> pd.DataFrame:
        q, args = "SELECT * FROM reviews WHERE 1=1", []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if status:
            q += " AND status=?"
            args.append(status)
        with self.s.conn() as c:
            return pd.read_sql_query(q + " ORDER BY id", c, params=args)

    def counts(self) -> dict[str, int]:
        df = self.items()
        out = {"pending": int((df.status == "pending").sum()) if not df.empty else 0}
        out["with_ai"] = int(((df.status == "pending") & df.llm_verdict.notna()).sum()) if not df.empty else 0
        out["decided"] = int(df.status.isin(["decided", "auto"]).sum()) if not df.empty else 0
        return out

    def decisions(self) -> dict[frozenset, str]:
        with self.s.conn() as c:
            return {frozenset((r["a"], r["b"])): r["decision"] for r in c.execute("SELECT * FROM match_decisions")}

    def approved_fixes(self) -> pd.DataFrame:
        with self.s.conn() as c:
            return pd.read_sql_query("SELECT * FROM approved_fixes ORDER BY approved_at DESC", c)

    # ---- AI step ---------------------------------------------------------------------------
    def analyze(self, llm: LLMClient, limit: int = 20, hide_personal: bool = False, auto_threshold: float = 0.0,
                progress: Callable[[int, int], None] | None = None) -> dict[str, int]:
        """Ask the model about pending items that have no opinion yet. Nothing is applied unless auto_threshold > 0."""
        todo = self.items(status="pending")
        todo = todo[todo.llm_verdict.isna()].head(limit)
        by_rid = {r.rid: r for rs in build_all(self.s).values() for r in rs}
        issues = self.s.issues().set_index("fingerprint")
        stats = {"analyzed": 0, "auto": 0, "skipped": 0, "failed": 0}
        for n, it in enumerate(todo.itertuples(), start=1):
            payload = json.loads(it.payload)
            try:
                if it.kind == "match":
                    a, b = by_rid.get(payload["a"]), by_rid.get(payload["b"])
                    if not (a and b):
                        self._close(it.id, "stale", "A record no longer exists in the data.")
                        stats["skipped"] += 1
                        continue
                    op = judge_match(llm, a, b, self.s, hide_personal)
                else:
                    row = issues.loc[payload["fingerprint"]].to_dict() if payload["fingerprint"] in issues.index else None
                    if row is None or row["status"] != "open":
                        self._close(it.id, "stale", "The issue is no longer open.")
                        stats["skipped"] += 1
                        continue
                    recs = [by_rid[x] for x in json.loads(row["record_ids"]) if x in by_rid]
                    op = review_issue(llm, {**row, "fingerprint": payload["fingerprint"]}, recs, self.s, hide_personal)
                self._set_ai(it.id, op, llm.model)
                stats["analyzed"] += 1
                if auto_threshold and it.kind == "match" and op.verdict in ("same", "different") and op.confidence >= auto_threshold:
                    self.decide(it.id, op.verdict, f"Auto-accepted: model {llm.model} was {op.confidence:.0%} confident", auto=True)
                    stats["auto"] += 1
            except Exception:  # noqa: BLE001  one bad item must not stop the batch
                stats["failed"] += 1
            if progress:
                progress(n, len(todo))
        return stats

    def _set_ai(self, rid: int, op, model: str) -> None:
        with self.s.conn() as c:
            c.execute("UPDATE reviews SET llm_verdict=?, llm_confidence=?, llm_reason=?, llm_extra=?, model=? WHERE id=?",
                      (op.verdict, op.confidence, op.reason, json.dumps(op.extra), model, rid))

    def _close(self, rid: int, status: str, note: str) -> None:
        with self.s.conn() as c:
            c.execute("UPDATE reviews SET status=?, note=?, decided_at=? WHERE id=?", (status, note, now(), rid))

    # ---- the human decision ---------------------------------------------------------------
    def decide(self, rid: int, decision: str, note: str = "", new_value: str | None = None, auto: bool = False) -> None:
        """match: same | different.   issue: real_problem | false_alarm | accept_fix (needs new_value)."""
        with self.s.conn() as c:
            r = c.execute("SELECT * FROM reviews WHERE id=?", (rid,)).fetchone()
            payload = json.loads(r["payload"])
            if r["kind"] == "match":
                if decision not in ("same", "different"):
                    raise ValueError("A match decision must be 'same' or 'different'.")
                a, b = pair_key(payload["a"], payload["b"])
                c.execute("INSERT OR REPLACE INTO match_decisions VALUES(?,?,?,?)", (a, b, decision, now()))
            else:
                fp = payload["fingerprint"]
                if decision == "false_alarm":
                    c.execute("UPDATE issues SET status='ignored', note=? WHERE fingerprint=?", (f"Reviewed: false alarm. {note}".strip(), fp))
                elif decision == "accept_fix":
                    if not (new_value or "").strip():
                        raise ValueError("Enter the corrected value to approve a fix.")
                    i = c.execute("SELECT * FROM issues WHERE fingerprint=?", (fp,)).fetchone()
                    if i is None:
                        raise ValueError("The issue no longer exists.")
                    src = i["source"] if i["source"] != "multiple" else (json.loads(i["details"] or "{}").get("correct_source") or "")
                    c.execute("INSERT OR REPLACE INTO approved_fixes VALUES(?,?,?,?,?,?,?,?,?)",
                              (fp, src, i["entity"], i["record_key"], i["field"], i["value"], new_value.strip(), note, now()))
                elif decision != "real_problem":
                    raise ValueError(f"Unknown decision '{decision}'")
            c.execute("UPDATE reviews SET status=?, decision=?, note=?, decided_at=? WHERE id=?",
                      ("auto" if auto else "decided", decision, note, now(), rid))

    def undo(self, rid: int) -> None:
        with self.s.conn() as c:
            r = c.execute("SELECT * FROM reviews WHERE id=?", (rid,)).fetchone()
            payload = json.loads(r["payload"])
            if r["kind"] == "match":
                a, b = pair_key(payload["a"], payload["b"])
                c.execute("DELETE FROM match_decisions WHERE a=? AND b=?", (a, b))
            else:
                c.execute("DELETE FROM approved_fixes WHERE fingerprint=?", (payload["fingerprint"],))
                if r["decision"] == "false_alarm":
                    c.execute("UPDATE issues SET status='open' WHERE fingerprint=? AND status='ignored'", (payload["fingerprint"],))
            c.execute("UPDATE reviews SET status='pending', decision=NULL, decided_at=NULL WHERE id=?", (rid,))

    # ---- report ---------------------------------------------------------------------------------
    def report(self, llm: LLMClient) -> str:
        run = self.s.latest_run()
        if not run:
            raise ValueError("Run a check first.")
        iss = self.s.issues()
        open_ = iss[iss.status == "open"]
        facts: dict[str, Any] = {
            "records_per_type": {k: {"rows": v["records"], "distinct": v["real_world_records"], "platforms": v["sources"]}
                                 for k, v in run["summary"]["entities"].items()},
            "open_issues_by_type": open_.groupby("rule").size().to_dict(),
            "open_issues_by_severity": open_.groupby("severity").size().to_dict(),
            "open_issues_by_platform": open_[open_.source != "multiple"].groupby("source").size().to_dict(),
            "example_messages": open_.sort_values("severity").groupby("rule").head(2).message.head(12).tolist(),
            "review_queue": self.counts()}
        text = write_report(llm, facts)
        self.s.set_setting("last_ai_report", text)
        return text
