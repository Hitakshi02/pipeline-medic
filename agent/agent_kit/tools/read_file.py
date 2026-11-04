"""read_file: read a file of the pipeline in your sandbox (models, config, contracts, tests)."""

DEFINITION = {
    "type": "function",
    "name": "read_file",
    "description": "Read a pipeline file in your sandbox with line numbers, e.g. models/marts/daily_revenue.sql, "
    "config.json, contracts/contracts.yaml, run_pipeline.py. Without `path`, lists the files. "
    "`original: true` reads the snapshot at failure time instead of your working copy.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string"},
            "original": {"type": "boolean"},
        },
        "required": [],
    },
}

MAX_CHARS = 20000


def run(ctx, args: dict) -> dict:
    path = str(args.get("path") or "").strip()
    if not path:
        return {"files": ctx.sandbox.listing()}
    base = ctx.sandbox.original if args.get("original") else ctx.sandbox.pipeline
    target = ctx.sandbox.resolve(path, base)
    if not target.is_file():
        return {"error": f"no such file: {path}"}
    text = target.read_text()[:MAX_CHARS]
    numbered = "\n".join(f"{i:>4}  {line}" for i, line in enumerate(text.splitlines(), 1))
    return {"path": path, "content": numbered}
