"""The incident record: everything the agent establishes during a run, and what people
decide about it. One JSON file per run at workspace/records/<run-id>.json, changed only
through this class; every change is appended to an audit trail.

The agent writes through tools (plan, queries, findings, patches, replays, requests,
RCA submissions); people write through review/release (answers, approve/reject).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

HYPOTHESIS_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_STORED_ROWS = 200


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class RecordError(ValueError):
    """A write the record refused. The message is safe to show the model."""


def _norm(value: Any) -> str:
    return str(value).strip().strip("'\"").lower()


def _matches(cited: str, cell: Any) -> bool:
    if cell is None:
        return _norm(cited) in ("null", "none", "")
    if _norm(cited) == _norm(cell):
        return True
    try:
        return abs(float(cited) - float(cell)) < 1e-9
    except (TypeError, ValueError):
        return False


class IncidentRecord:
    def __init__(self, workspace: Path, run_id: str, incident_id: str = "") -> None:
        self.path = Path(workspace) / "records" / f"{run_id}.json"
        if self.path.is_file():
            self.data: dict = json.loads(self.path.read_text())
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = {
            "run_id": run_id, "incident_id": incident_id, "created": now(),
            "plan": {}, "plan_checks": [], "queries": [], "findings": [], "rejected_findings": [],
            "patches": [], "replays": [], "requests": [], "submissions": [], "rca": None,
            "review": None, "audit": [],
        }
        self._save("created", "runtime", {})

    # ---- persistence ----
    def _save(self, action: str, actor: str, detail: dict) -> None:
        self.data["audit"].append({"at": now(), "actor": actor, "action": action, "detail": detail})
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str))
        tmp.replace(self.path)

    def require_plan(self, what: str) -> None:
        if not self.data["plan"]:
            raise RecordError(f"set your investigation plan first (set_investigation_plan) before you {what}")

    # ---- plan ----
    def parse_plan(self, hypotheses: Any) -> dict:
        if not isinstance(hypotheses, list) or not hypotheses:
            raise RecordError("the plan needs at least one hypothesis")
        plan: dict = {}
        for raw in hypotheses:
            if not isinstance(raw, dict):
                raise RecordError("each hypothesis must be an object")
            hid = str(raw.get("id", "")).strip()
            if not HYPOTHESIS_ID.match(hid):
                raise RecordError(f"hypothesis id {hid!r} must be snake_case, e.g. upstream_rename")
            if hid in plan:
                raise RecordError(f"duplicate hypothesis id {hid}")
            text = str(raw.get("hypothesis", "")).strip()
            test = str(raw.get("how_to_test", "")).strip()
            if not text or not test:
                raise RecordError(f"{hid}: give both the hypothesis and how_to_test")
            old = self.data["plan"].get(hid, {})
            plan[hid] = {"hypothesis": text, "how_to_test": test,
                         "status": old.get("status", "open"), "findings": old.get("findings", [])}
        kept = [h for h, v in self.data["plan"].items() if v["findings"] and h not in plan]
        if kept:
            raise RecordError(f"keep hypotheses that already have findings: {', '.join(kept)}")
        return plan

    def set_plan(self, hypotheses: list) -> dict:
        self.data["plan"] = self.parse_plan(hypotheses)
        self._save("plan_set", "agent", {"hypotheses": list(self.data["plan"])})
        return self.data["plan"]

    def record_plan_check(self, result: dict) -> None:
        self.data["plan_checks"].append(dict(result, at=now()))
        self._save("plan_checked", "verification", {"passed": result.get("passed"), "accepted": result.get("accepted")})

    # ---- queries ----
    def log_query(self, sql: str, target: str, columns: list, rows: list, row_count: int,
                  error: str | None = None) -> dict:
        entry = {"id": f"Q{len(self.data['queries']) + 1}", "sql": sql, "target": target, "columns": columns,
                 "rows": rows[:MAX_STORED_ROWS], "row_count": row_count, "error": error, "at": now()}
        self.data["queries"].append(entry)
        self._save("query", "agent", {"id": entry["id"], "error": bool(error)})
        return entry

    def query(self, query_id: str) -> dict | None:
        return next((q for q in self.data["queries"] if q["id"] == str(query_id).strip()), None)

    def value_in_query(self, value: str, query_id: str) -> bool:
        q = self.query(query_id)
        if not q or q["error"]:
            return False
        if any(_norm(value) == _norm(c) for c in q["columns"]):
            return True
        return any(_matches(value, cell) for row in q["rows"] for cell in row)

    # ---- findings ----
    def record_finding(self, hypothesis: str, claim: str, verdict: str, query_ids: list, cited_values: list) -> dict:
        self.require_plan("record findings")
        if hypothesis not in self.data["plan"]:
            raise RecordError(f"unknown hypothesis {hypothesis!r}; your plan has: {', '.join(self.data['plan'])}")
        if verdict not in ("supports", "refutes"):
            raise RecordError("verdict must be 'supports' or 'refutes'")
        if not str(claim).strip():
            raise RecordError("the claim is empty")
        query_ids = [str(q).strip() for q in query_ids or []]
        cited_values = [str(v) for v in cited_values or []]
        problems = []
        if not query_ids:
            problems.append("cite at least one logged query id (from query_data)")
        for qid in query_ids:
            q = self.query(qid)
            if q is None:
                problems.append(f"{qid} is not a logged query")
            elif q["error"]:
                problems.append(f"{qid} failed, so it proves nothing")
        if not cited_values:
            problems.append("cite at least one value that appears in the cited query results")
        for value in cited_values:
            if not any(self.value_in_query(value, qid) for qid in query_ids):
                problems.append(f"the value {value!r} does not appear in the results of {', '.join(query_ids) or 'the cited queries'}")
        if problems:
            self.data["rejected_findings"].append({"hypothesis": hypothesis, "claim": claim, "problems": problems, "at": now()})
            self._save("finding_rejected", "agent", {"hypothesis": hypothesis})
            raise RecordError("finding refused: " + "; ".join(problems))
        finding = {"id": f"F{len(self.data['findings']) + 1}", "hypothesis": hypothesis, "claim": claim,
                   "verdict": verdict, "query_ids": query_ids, "cited_values": cited_values, "at": now()}
        self.data["findings"].append(finding)
        entry = self.data["plan"][hypothesis]
        entry["findings"].append(finding["id"])
        entry["status"] = "supported" if verdict == "supports" else ("refuted" if entry["status"] == "open" else entry["status"])
        self._save("finding", "agent", {"id": finding["id"], "hypothesis": hypothesis})
        return finding

    # ---- patches and replays ----
    def add_patch(self, path: str, reason: str, diff: str) -> dict:
        patch = {"id": f"P{len(self.data['patches']) + 1}", "path": path, "reason": reason, "diff": diff, "at": now()}
        self.data["patches"].append(patch)
        self._save("patch", "agent", {"id": patch["id"], "path": path})
        return patch

    def add_replay(self, summary: dict) -> dict:
        answered = sum(1 for r in self.data["requests"] if r["status"] == "answered")
        replay = dict(summary, id=f"R{len(self.data['replays']) + 1}", after_patches=len(self.data["patches"]),
                      after_answers=answered, at=now())
        self.data["replays"].append(replay)
        self._save("replay", "agent", {"id": replay["id"], "status": summary.get("status")})
        return replay

    def last_replay(self) -> dict | None:
        return self.data["replays"][-1] if self.data["replays"] else None

    # ---- requests ----
    def open_request(self, question: str, kind: str = "upstream") -> dict:
        request = {"id": f"REQ{len(self.data['requests']) + 1}", "kind": kind, "question": question,
                   "status": "open", "response": None, "opened": now(), "answered": None}
        self.data["requests"].append(request)
        self._save("request_opened", "agent" if kind == "upstream" else "runtime", {"id": request["id"], "kind": kind})
        return request

    def open_requests(self) -> list:
        return [r for r in self.data["requests"] if r["status"] == "open"]

    def answer_request(self, request_id: str, response: str, actor: str = "human") -> dict:
        for request in self.data["requests"]:
            if request["id"] == request_id:
                if request["status"] != "open":
                    raise RecordError(f"{request_id} is already answered")
                request.update(status="answered", response=response, answered=now())
                self._save("request_answered", actor, {"id": request_id})
                return request
        raise RecordError(f"no request {request_id}")

    # ---- submissions and review ----
    def add_submission(self, rca: dict, checks: list, outcome: str) -> dict:
        entry = {"attempt": len(self.data["submissions"]) + 1, "rca": rca, "checks": checks, "outcome": outcome, "at": now()}
        self.data["submissions"].append(entry)
        if outcome in ("accepted", "unverified"):
            self.data["rca"] = dict(rca, status=outcome, submitted=now())
        self._save("rca_submitted", "agent", {"attempt": entry["attempt"], "outcome": outcome})
        return entry

    def refusals(self) -> int:
        """Refused submissions since the last escalation."""
        count = 0
        for s in reversed(self.data["submissions"]):
            if s["outcome"] == "escalated":
                break
            if s["outcome"] == "refused":
                count += 1
        return count

    def review(self, approve: bool, notes: str = "") -> dict:
        if not self.data["rca"] or self.data["rca"]["status"] != "accepted":
            raise RecordError("there is no verified RCA awaiting review")
        self.data["review"] = {"decision": "approved" if approve else "rejected", "notes": notes, "at": now()}
        if not approve:
            self.data["rca"]["status"] = "rejected"
        self._save("reviewed", "human", {"decision": self.data["review"]["decision"]})
        return self.data["review"]

    def summary(self) -> dict:
        return {
            "plan": {k: {"status": v["status"], "findings": v["findings"]} for k, v in self.data["plan"].items()},
            "queries": len(self.data["queries"]), "findings": len(self.data["findings"]),
            "patches": len(self.data["patches"]), "replays": len(self.data["replays"]),
            "open_requests": [r["id"] for r in self.open_requests()],
            "submissions": len(self.data["submissions"]),
            "rca": (self.data["rca"] or {}).get("status"),
        }
