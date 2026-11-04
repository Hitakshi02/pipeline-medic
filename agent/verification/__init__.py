"""The verification layer (VERIFICATION=on). One file per check, one entry point each:

    run_verification(ctx, work) -> {"passed": bool, "feedback": ..., ...}

- verify_plan.py    set_investigation_plan: does the plan cover the kinds of hypothesis this incident needs?
- verify_rca.py     submit_rca, code only: claims cite logged queries, locations are real, replay after the last change
- verify_fix.py     submit_rca, code only: the proof. Replay vs golden outputs and hidden invariants; cheat-fix detection
- verify_claims.py  submit_rca, one model call: is each claim supported by its cited query results?

The tools call these at fixed points, so the agent cannot skip them. The agent sees only
`feedback`. Ground truth is read only through ground_truth.load().
"""
