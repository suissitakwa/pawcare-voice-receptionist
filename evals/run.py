"""Run the scripted evaluations.

From backend/, with the database running and an OpenAI key in the environment only:

    OPENAI_API_KEY=... uv run python ../evals/run.py
    OPENAI_API_KEY=... uv run python ../evals/run.py --scenario book-happy-path --repeat 3

Writes evals/results/<timestamp>.json and evals/results/<timestamp>.md.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DATABASE = "postgresql+psycopg://pawcare:pawcare@localhost:5440/pawcare_eval"


def prepare_database(url: str) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    target = make_url(url)
    if not target.database.endswith("_eval"):
        sys.exit("Refusing to run: the evaluation database name must end with _eval.")
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
        )
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()
    command.upgrade(Config(str(HERE.parent / "backend" / "alembic.ini")), "head")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="append", help="Run only these scenario ids")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default=os.environ.get("VOICE_MODEL", "gpt-realtime-2.1-mini"))
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        sys.exit("Set OPENAI_API_KEY in the environment for this command only.")
    url = os.environ.get("EVAL_DATABASE_URL", DEFAULT_DATABASE)
    # Settings are read once, so point the app at the evaluation database before importing it.
    os.environ["DATABASE_URL"] = url
    sys.path.insert(0, str(HERE.parent / "backend"))
    sys.path.insert(0, str(HERE))
    prepare_database(url)

    from pawcare_evals.checks import evaluate
    from pawcare_evals.harness import run_scenario
    from pawcare_evals.model import RealtimeTextModel
    from pawcare_evals.report import render
    from sqlalchemy.orm import Session

    from app.db import get_engine
    from app.seed import seed_business

    spec = json.loads((HERE / "scenarios.json").read_text())
    today = datetime.fromisoformat(spec["today"])
    scenarios = [s for s in spec["scenarios"] if not args.scenario or s["id"] in args.scenario]
    if not scenarios:
        sys.exit("No matching scenarios.")

    runs = []
    with Session(get_engine()) as session:
        seed_business(session)
        session.commit()
        for scenario in scenarios:
            for attempt in range(args.repeat):
                record = await run_scenario(
                    session, RealtimeTextModel(api_key, args.model), scenario, today
                )
                checks = evaluate(record, scenario, session)
                passed = all(c["passed"] for c in checks)
                label = "PASS" if passed else "FAIL"
                print(f"{label}  {scenario['id']} ({attempt + 1}/{args.repeat})")
                for c in checks:
                    if not c["passed"]:
                        print(f"      {c['type']}: {c['detail']}")
                runs.append(
                    {
                        "id": scenario["id"],
                        "category": scenario["category"],
                        "attempt": attempt + 1,
                        "checks": checks,
                        "transcript": record.entries,
                        "tool_calls": len(record.tool_calls),
                        "tool_failures": sum(not c["ok"] for c in record.tool_calls),
                        "first_output_ms": [r["first_output_ms"] for r in record.replies],
                        "input_tokens": sum(
                            r["usage"].get("input_tokens", 0) for r in record.replies
                        ),
                        "output_tokens": sum(
                            r["usage"].get("output_tokens", 0) for r in record.replies
                        ),
                        "error": record.error,
                    }
                )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results = {
        "model": args.model,
        "run_at": stamp,
        "today": spec["today"],
        "scenario_count": len(scenarios),
        "runs": runs,
    }
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{stamp}.json").write_text(json.dumps(results, indent=2, default=str))
    (out / f"{stamp}.md").write_text(render(results))
    print(f"\nReport: evals/results/{stamp}.md")


if __name__ == "__main__":
    asyncio.run(main())
