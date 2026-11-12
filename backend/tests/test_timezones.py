"""Bare dates are local days in Europe/Budapest (ADR-0006), including DST switch days."""

import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import timeutil
from app.timeutil import parse_lower_bound, parse_upper_bound

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# ---------- Bounds ----------


@pytest.mark.parametrize(
    ("day", "start", "end"),
    [
        # Winter (CET, +01:00)
        ("2026-11-01", "2026-10-31T23:00:00", "2026-11-01T22:59:59.999999"),
        # Summer (CEST, +02:00)
        ("2026-08-01", "2026-07-31T22:00:00", "2026-08-01T21:59:59.999999"),
        # Spring forward: 2026-03-29 has 23 hours
        ("2026-03-29", "2026-03-28T23:00:00", "2026-03-29T21:59:59.999999"),
        # Fall back: 2026-10-25 has 25 hours
        ("2026-10-25", "2026-10-24T22:00:00", "2026-10-25T22:59:59.999999"),
    ],
)
def test_bare_date_is_a_local_day(day: str, start: str, end: str) -> None:
    assert parse_lower_bound(day) == utc(start)
    assert parse_upper_bound(day) == utc(end)


def test_datetimes_with_offset_are_kept_and_naive_ones_are_local() -> None:
    assert parse_lower_bound("2026-11-01T00:30:00+01:00") == utc("2026-10-31T23:30:00")
    assert parse_lower_bound("2026-11-01T00:30:00Z") == utc("2026-11-01T00:30:00")
    assert parse_upper_bound("2026-07-01T12:00:00") == utc("2026-07-01T10:00:00")


def test_month_start_is_local() -> None:
    now = utc("2026-10-31T23:45:00")  # 00:45 on 1 November in Budapest
    assert timeutil.local_today(now).isoformat() == "2026-11-01"
    assert timeutil.local_midnight_utc(timeutil.local_today(now).replace(day=1)) == utc(
        "2026-10-31T23:00:00"
    )


# ---------- API ----------


@pytest.fixture()
def freeze(monkeypatch: pytest.MonkeyPatch):
    def _freeze(iso_utc: str) -> None:
        monkeypatch.setattr(timeutil, "now_utc", lambda: utc(iso_utc))

    return _freeze


def _add(client: TestClient, category_id: int, occurred_at: str, amount: int) -> dict[str, Any]:
    resp = client.post(
        "/api/expenses",
        json={
            "amount": amount,
            "currency": "HUF",
            "category_id": category_id,
            "occurred_at": occurred_at,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _total(client: TestClient, date_from: str, date_to: str) -> int:
    resp = client.get("/api/stats/summary", params={"date_from": date_from, "date_to": date_to})
    assert resp.status_code == 200, resp.text
    return int(resp.json()["total_huf"])


def _listed(client: TestClient, date_from: str, date_to: str) -> list[int]:
    resp = client.get("/api/expenses", params={"date_from": date_from, "date_to": date_to})
    assert resp.status_code == 200, resp.text
    return sorted(int(e["amount_huf"]) for e in resp.json()["items"])


def test_half_past_midnight_on_the_first_belongs_to_the_new_month(
    admin: TestClient, category_id: int, freeze
) -> None:
    _add(admin, category_id, "2026-10-31T23:30:00Z", 1)  # 1 Nov 00:30 local
    _add(admin, category_id, "2026-10-31T22:59:00Z", 10)  # 31 Oct 23:59 local

    assert _listed(admin, "2026-11-01", "2026-11-01") == [1]
    assert _listed(admin, "2026-10-31", "2026-10-31") == [10]
    assert _total(admin, "2026-11-01", "2026-11-30") == 1

    freeze("2026-10-31T23:45:00")  # 1 Nov 00:45 local: "this month" is November
    budget = admin.get("/api/stats/budget").json()
    assert budget["spent_huf"] == 1
    summary = admin.get("/api/stats/summary").json()  # default period = current local month
    assert summary["total_huf"] == 1

    freeze("2026-11-12T09:00:00")
    months = {m["month"]: m["total_huf"] for m in admin.get("/api/stats/by-month").json()}
    assert months["2026-11"] == 1
    assert months["2026-10"] == 10


def test_summer_month_boundary(admin: TestClient, category_id: int, freeze) -> None:
    _add(admin, category_id, "2026-07-31T22:30:00Z", 1)  # 1 Aug 00:30 CEST
    _add(admin, category_id, "2026-07-31T21:30:00Z", 10)  # 31 Jul 23:30 CEST
    assert _total(admin, "2026-08-01", "2026-08-31") == 1
    assert _total(admin, "2026-07-01", "2026-07-31") == 10
    freeze("2026-08-20T10:00:00")
    months = {m["month"]: m["total_huf"] for m in admin.get("/api/stats/by-month").json()}
    assert (months["2026-08"], months["2026-07"]) == (1, 10)


def test_spring_forward_day(admin: TestClient, category_id: int) -> None:
    _add(admin, category_id, "2026-03-28T22:59:00Z", 1000)  # 28 Mar 23:59 CET
    _add(admin, category_id, "2026-03-28T23:00:00Z", 1)  # 29 Mar 00:00 CET
    _add(admin, category_id, "2026-03-29T21:59:59Z", 10)  # 29 Mar 23:59:59 CEST
    _add(admin, category_id, "2026-03-29T22:00:00Z", 100)  # 30 Mar 00:00 CEST
    assert _listed(admin, "2026-03-29", "2026-03-29") == [1, 10]
    assert _total(admin, "2026-03-29", "2026-03-29") == 11
    assert _total(admin, "2026-03-30", "2026-03-30") == 100


def test_yesterday_on_the_fall_back_day(admin: TestClient, category_id: int, freeze) -> None:
    """The bot asks for "yesterday" with date_from = date_to = yesterday's local date."""
    _add(admin, category_id, "2026-10-24T21:59:00Z", 1000)  # 24 Oct 23:59 CEST
    _add(admin, category_id, "2026-10-24T22:30:00Z", 1)  # 25 Oct 00:30 CEST
    _add(admin, category_id, "2026-10-25T00:30:00Z", 10)  # 25 Oct 02:30 CEST (first pass)
    _add(admin, category_id, "2026-10-25T01:30:00Z", 100)  # 25 Oct 02:30 CET (second pass)
    _add(admin, category_id, "2026-10-25T22:30:00Z", 1000_0)  # 25 Oct 23:30 CET
    _add(admin, category_id, "2026-10-25T23:00:00Z", 100_000)  # 26 Oct 00:00 CET

    freeze("2026-10-26T07:00:00")  # 26 Oct 08:00 local
    yesterday = "2026-10-25"
    assert _total(admin, yesterday, yesterday) == 1 + 10 + 100 + 10_000
    summary = admin.get(
        "/api/stats/summary", params={"date_from": yesterday, "date_to": yesterday}
    ).json()
    assert summary["count"] == 4
    assert summary["avg_per_day"] == 10_111.0  # one local day, though it lasted 25 hours


def test_export_uses_local_days_and_has_source(admin: TestClient, category_id: int) -> None:
    _add(admin, category_id, "2026-10-31T23:30:00Z", 1)
    _add(admin, category_id, "2026-10-31T22:59:00Z", 10)
    resp = admin.get(
        "/api/expenses/export", params={"date_from": "2026-11-01", "date_to": "2026-11-01"}
    )
    assert resp.status_code == 200
    lines = resp.text.lstrip("﻿").strip().splitlines()
    assert lines[0].endswith(",source")
    assert len(lines) == 2
    assert lines[1].endswith(",web")


def test_invalid_date_filter_is_422(admin: TestClient) -> None:
    assert admin.get("/api/expenses", params={"date_from": "01.11.2026"}).status_code == 422
    assert admin.get("/api/stats/summary", params={"date_to": "2026-13-01"}).status_code == 422


# ---------- occurred_at without an offset ----------


def test_token_rejects_naive_occurred_at(bot: TestClient, category_id: int) -> None:
    resp = bot.post(
        "/api/expenses",
        json={
            "amount": 100,
            "currency": "HUF",
            "category_id": category_id,
            "occurred_at": "2026-11-10T09:15:00",
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "occurred_at must include a timezone offset"


def test_token_rejects_naive_occurred_at_on_patch(
    admin: TestClient, bot: TestClient, category_id: int
) -> None:
    expense = _add(admin, category_id, "2026-11-10T08:00:00Z", 100)
    resp = bot.patch(f"/api/expenses/{expense['id']}", json={"occurred_at": "2026-11-10T10:00"})
    assert resp.status_code == 422
    resp = bot.patch(
        f"/api/expenses/{expense['id']}", json={"occurred_at": "2026-11-10T10:00:00+01:00"}
    )
    assert resp.status_code == 200
    assert resp.json()["occurred_at"] == "2026-11-10T09:00:00+00:00"


def test_site_sends_utc_iso_and_naive_stays_utc(admin: TestClient, category_id: int) -> None:
    # What the browser sends: Date.prototype.toISOString() → "…Z" (see budapestInputToIso).
    sent = _add(admin, category_id, "2026-10-31T23:30:00.000Z", 1)
    assert sent["occurred_at"] == "2026-10-31T23:30:00+00:00"
    # Old clients without an offset keep the previous behaviour (UTC).
    naive = _add(admin, category_id, "2026-10-31T23:30:00", 2)
    assert naive["occurred_at"] == "2026-10-31T23:30:00+00:00"
    # Any offset is normalised to UTC in storage.
    offset = _add(admin, category_id, "2026-11-01T00:30:00+01:00", 3)
    assert offset["occurred_at"] == "2026-10-31T23:30:00+00:00"


NODE_SCRIPT = """
import { budapestInputToIso, isoToBudapestInput } from "./common.js";
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map((c) => [budapestInputToIso(c), isoToBudapestInput(
  budapestInputToIso(c))])));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("tz", ["UTC", "Asia/Dubai", "America/New_York"])
def test_frontend_converts_budapest_input_to_utc_iso(tmp_path: Path, tz: str) -> None:
    """The browser form sends UTC ISO strings whatever the device's own time zone is."""
    for name in ("common.js", "api.js"):
        shutil.copy(FRONTEND / "js" / name, tmp_path / name)
    (tmp_path / "package.json").write_text('{"type": "module"}')
    (tmp_path / "check.js").write_text(NODE_SCRIPT)
    cases = ["2026-11-01T00:30", "2026-07-15T12:00", "2026-03-29T03:30", "2026-10-25T01:30"]
    node = shutil.which("node")
    assert node is not None
    out = subprocess.run(
        [node, "check.js", json.dumps(cases)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "TZ": tz},
        timeout=30,
    )
    results = json.loads(out.stdout)
    assert [iso for iso, _ in results] == [
        "2026-10-31T23:30:00.000Z",
        "2026-07-15T10:00:00.000Z",
        "2026-03-29T01:30:00.000Z",
        "2026-10-24T23:30:00.000Z",
    ]
    assert [back for _, back in results] == cases
