"""The engineer's sign-off: approve (writes patch.diff and INCIDENT_REPORT.md, run done)
or reject with notes (the agent revises on resume); answer an escalation or request."""

from __future__ import annotations

from pathlib import Path

from . import progress
from .incident_record import IncidentRecord, RecordError


def reports_dir(workspace: Path, run_id: str) -> Path:
    return Path(workspace) / "reports" / run_id


def approve(workspace: Path, run_id: str, notes: str = "") -> Path:
    state = progress.load(workspace, run_id)
    if state["status"] != "awaiting_review":
        raise RecordError(f"the run is {state['status']}, not awaiting_review")
    record = IncidentRecord(workspace, run_id)
    record.review(True, notes)
    folder = write_report(workspace, run_id)
    progress.set_status(workspace, run_id, "done")
    return folder


def reject(workspace: Path, run_id: str, notes: str) -> None:
    state = progress.load(workspace, run_id)
    if state["status"] != "awaiting_review":
        raise RecordError(f"the run is {state['status']}, not awaiting_review")
    if not notes.strip():
        raise RecordError("say what to change (notes)")
    IncidentRecord(workspace, run_id).review(False, notes)


def answer(workspace: Path, run_id: str, request_id: str, text: str) -> dict:
    return IncidentRecord(workspace, run_id).answer_request(request_id, text)


def write_report(workspace: Path, run_id: str) -> Path:
    record = IncidentRecord(workspace, run_id).data
    state = progress.load(workspace, run_id)
    rca = record["rca"] or {}
    folder = reports_dir(workspace, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "patch.diff").write_text(rca.get("diff") or "")
    lines = [f"# Incident report: {record['incident_id']}", "",
             f"Run `{run_id}` · model {state['model']} · effort {state.get('effort')} · "
             f"verification {'on' if state['verification'] else 'off'} · {state['turn']} turns", "",
             "## Summary", "", rca.get("summary", ""), "", "## Root causes", ""]
    for cause in rca.get("root_causes") or []:
        loc = cause.get("location") or {}
        where = ", ".join(f"{k}={v}" for k, v in loc.items() if k != "kind")
        lines.append(f"- **{cause.get('fault_type')}** ({loc.get('kind')}{': ' + where if where else ''}): "
                     f"{cause.get('explanation', '')} [{', '.join(cause.get('query_ids') or [])}]")
    lines += ["", "## Evidence", ""]
    queries = {q["id"]: q for q in record["queries"]}
    for claim in rca.get("claims") or []:
        lines.append(f"- {claim.get('claim')} [{', '.join(claim.get('query_ids') or [])}]")
    cited = sorted({q for c in (rca.get("claims") or []) + (rca.get("root_causes") or []) for q in c.get("query_ids") or []},
                   key=lambda q: int(q[1:]) if q[1:].isdigit() else 0)
    for qid in cited:
        if qid in queries:
            lines += ["", f"**{qid}** (`{queries[qid]['target']}`)", "", "```sql", queries[qid]["sql"].strip(), "```"]
    lines += ["", "## Fix", "", rca.get("fix", ""), "", "```diff", rca.get("diff") or "(no changes)", "```", "",
              "## Verification", ""]
    last = record["submissions"][-1] if record["submissions"] else {}
    for check in last.get("checks", []):
        lines.append(f"- {check.get('check')}: {'passed' if check.get('passed') else 'failed'}")
    review = record.get("review") or {}
    lines += ["", "## Sign-off", "", f"{review.get('decision', 'pending')} at {review.get('at', '')}"
              + (f": {review['notes']}" if review.get("notes") else ""), ""]
    (folder / "INCIDENT_REPORT.md").write_text("\n".join(lines))
    return folder
