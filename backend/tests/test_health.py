"""/api/health reports DB status and the build version."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.main as main_module


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(main_module, "ensure_today_rates", lambda db: False)
    monkeypatch.setattr(main_module.settings, "app_version", "9.9.9")
    monkeypatch.setattr(main_module.settings, "git_sha", "abc1234")
    with TestClient(main_module.app) as c:
        yield c


def test_health_reports_version(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["version"] == "9.9.9"
    assert body["git_sha"] == "abc1234"
