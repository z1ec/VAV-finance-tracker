"""docs/openapi.json is the bot's contract: it must match the running app exactly."""

import json
import tomllib
from pathlib import Path

from app import API_VERSION
from app.main import app

ROOT = Path(__file__).resolve().parents[2]


def test_openapi_json_is_up_to_date() -> None:
    expected = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    current = (ROOT / "docs" / "openapi.json").read_text(encoding="utf-8")
    assert current == expected, (
        "docs/openapi.json is stale: run backend/.venv/bin/python scripts/export_openapi.py"
    )


def test_api_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == API_VERSION
    assert app.version == API_VERSION


def test_bot_endpoints_document_idempotency_key() -> None:
    paths = app.openapi()["paths"]
    for path in ("/api/expenses", "/api/subscriptions/charges/{charge_id}/pay"):
        names = {p["name"] for p in paths[path]["post"]["parameters"]}
        assert "Idempotency-Key" in names
