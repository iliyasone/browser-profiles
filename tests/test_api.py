import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.runtime import Settings
from app.store import ProfileSpec, Store


class FakeRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.running: set[str] = set()

    def status(self, name: str) -> str:
        return "running" if name in self.running else "absent"

    def start(self, spec: ProfileSpec) -> None:
        self.running.add(spec.name)

    def viewers(self, name: str) -> int:
        return 0

    def stop(self, name: str) -> None:
        self.running.discard(name)

    def remove(self, name: str) -> None:
        self.running.discard(name)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setitem(os.environ, "API_TOKEN", "secret")
    settings = Settings.from_env()
    main.app.state.runtime = FakeRuntime(
        replace(settings, data_dir=tmp_path, host_data_dir=Path("/srv/x"))
    )
    main.app.state.store = Store(tmp_path)
    return TestClient(main.app)


def test_token_required(client: TestClient) -> None:
    assert client.get("/profiles").status_code == 401
    assert (
        client.get("/profiles", headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )
    assert client.get("/healthz").status_code == 200


def test_lifecycle(client: TestClient) -> None:
    auth = {"Authorization": "Bearer secret"}
    created = client.post(
        "/profiles",
        json={
            "name": "one",
            "proxy": {"host": "h", "port": 9, "username": "u", "password": "p"},
        },
        headers=auth,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "running"
    assert body["proxy"] == "http://u:***@h:9"
    assert body["ui_url"].endswith("/b/one/")
    assert body["data_dir"] == "/srv/x/one"

    assert (
        client.post("/profiles", json={"name": "one"}, headers=auth).status_code == 409
    )
    assert client.post("/profiles/one/stop", headers=auth).json()["status"] == "absent"
    assert (
        client.patch("/profiles/one", json={"clear_proxy": True}, headers=auth).json()[
            "proxy"
        ]
        is None
    )
    assert [p["name"] for p in client.get("/profiles", headers=auth).json()] == ["one"]
    assert client.delete("/profiles/one?purge=true", headers=auth).status_code == 204
    assert client.get("/profiles/one", headers=auth).status_code == 404
