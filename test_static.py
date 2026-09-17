import os

os.environ["DATABASE_URL"] = "sqlite:///./test_loadmatch.db"
os.environ.setdefault("STATIC_DIR", "./test_static")

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "static" in body


def test_root_serves_index():
    r = client.get("/")
    assert r.status_code == 200
    assert "waynook" in r.text


def test_docs_available():
    assert client.get("/docs").status_code == 200
