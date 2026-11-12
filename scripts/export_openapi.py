#!/usr/bin/env python3
"""Write the backend's OpenAPI schema to docs/openapi.json (the contract read by the bot).

    backend/.venv/bin/python scripts/export_openapi.py          # write
    backend/.venv/bin/python scripts/export_openapi.py --check  # exit 1 if the file is stale

CI runs the check through backend/tests/test_openapi.py.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "openapi.json"


def render() -> str:
    # Importing the app must not touch a real database or create data/ next to the caller.
    os.environ.setdefault("DB_PATH", str(Path(tempfile.gettempdir()) / "openapi-export.db"))
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app

    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if docs/openapi.json is stale")
    args = parser.parse_args()
    schema = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != schema:
            print(f"{OUTPUT.relative_to(ROOT)} is stale: run scripts/export_openapi.py")
            return 1
        print(f"{OUTPUT.relative_to(ROOT)} is up to date")
        return 0
    OUTPUT.write_text(schema, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
