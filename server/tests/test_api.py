"""API tests. Run against an in-memory MongoDB fake (mongomock), so no server is needed.
Note: mongomock does not enforce $jsonSchema validators; those are enforced by real MongoDB."""
import importlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("BLIND_MONGO_URI", "mongomock://")
    from app import db, main
    importlib.reload(db)       # fresh in-memory database per test
    importlib.reload(main)
    from app.security import hash_password, sha256
    with TestClient(main.app) as client:   # runs startup -> init_db
        d = db.get_db()
        d.users.insert_one({"username": "nurse", "pw_hash": hash_password("correct-horse"), "created_at": db.now()})
        d.devices.insert_one({"_id": "kria-01", "name": "Room 1", "key_hash": sha256("devkey"),
                              "created_at": db.now(), "last_seen": None, "last_epoch": -1, "last_seq": -1,
                              "current_state": None})
        yield client, db


def send(client, state, seq, epoch=0, key="devkey"):
    return client.post("/api/device/kria-01/state", json={"epoch": epoch, "seq": seq, "state": state},
                       headers={"Authorization": f"Bearer {key}"})


def login(client):
    assert client.post("/api/auth/login", json={"username": "nurse", "password": "correct-horse"}).status_code == 200


def test_device_needs_valid_key(env):
    client, _ = env
    assert send(client, "IDLE", 0, key="wrong").status_code == 401
    assert client.post("/api/device/kria-01/state", json={"epoch": 0, "seq": 0, "state": "IDLE"}).status_code == 401
    assert client.post("/api/device/nope/state", json={"epoch": 0, "seq": 0, "state": "IDLE"},
                       headers={"Authorization": "Bearer devkey"}).status_code == 401


def test_rejects_extra_fields_and_big_frames(env):
    client, _ = env
    h = {"Authorization": "Bearer devkey"}
    assert client.post("/api/device/kria-01/state", headers=h,
                       json={"epoch": 0, "seq": 0, "state": "IDLE", "image": "AAAA"}).status_code == 422
    assert client.post("/api/device/kria-01/state", headers=h, content=b"x" * 1000).status_code == 413
    assert client.post("/api/device/kria-01/state", headers=h,
                       json={"epoch": 0, "seq": 0, "state": "DANCING"}).status_code == 422


def test_transitions_store_current_and_past_state(env):
    client, db = env
    for i, s in enumerate(["IDLE", "IDLE", "FALLING", "LAYING", "LAYING", "ALERT", "IDLE"]):
        assert send(client, s, i).status_code == 200
    assert send(client, "ALERT", 3).json()["duplicate"] is True          # replay of an old seq
    assert send(client, "IDLE", 0, epoch=1).json()["duplicate"] is False  # reboot -> new epoch
    d = db.get_db()
    ev = list(d.events.find().sort("_id", 1))
    assert [(e["prev_state"], e["state"]) for e in ev] == [
        (None, "IDLE"), ("IDLE", "FALLING"), ("FALLING", "LAYING"), ("LAYING", "ALERT"), ("ALERT", "IDLE")]
    assert set(ev[0].keys()) == {"_id", "device_id", "state", "prev_state", "ts"}   # nothing else stored
    dev = d.devices.find_one({"_id": "kria-01"})
    assert dev["current_state"] == "IDLE" and (dev["last_epoch"], dev["last_seq"]) == (1, 0)


def test_dashboard_requires_login(env):
    client, _ = env
    for path in ["/api/devices", "/api/events", "/api/alerts", "/api/stats", "/api/privacy", "/api/auth/me"]:
        assert client.get(path).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nurse", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "ghost", "password": "nope"}).status_code == 401


def test_login_logout_and_expiry(env):
    client, db = env
    login(client)
    assert client.get("/api/auth/me").json()["username"] == "nurse"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    login(client)
    db.get_db().sessions.update_many({}, {"$set": {"expires_at": db.now() - timedelta(seconds=1)}})
    assert client.get("/api/auth/me").status_code == 401


def test_alert_flow(env):
    client, _ = env
    for i, s in enumerate(["IDLE", "FALLING", "LAYING", "ALERT"]):
        send(client, s, i)
    login(client)
    dev = client.get("/api/devices").json()["devices"][0]
    assert dev["current_state"] == "ALERT" and dev["online"] and dev["open_alert"]
    alerts = client.get("/api/alerts?status=open").json()["alerts"]
    assert len(alerts) == 1
    assert client.post(f"/api/alerts/{alerts[0]['id']}/ack").status_code == 200
    assert client.post(f"/api/alerts/{alerts[0]['id']}/ack").status_code == 200   # idempotent
    assert client.post("/api/alerts/not-an-id/ack").status_code == 404
    assert client.get("/api/alerts?status=open").json()["alerts"] == []
    assert client.get("/api/alerts").json()["alerts"][0]["acked_by"] == "nurse"
    assert client.get("/api/devices").json()["devices"][0]["open_alert"] is False
    b = client.get("/api/stats?hours=24").json()["buckets"]
    assert sum(x["alerts"] for x in b) == 1 and sum(x["falls"] for x in b) == 1
    ev = client.get("/api/events?device_id=kria-01").json()["events"]
    assert ev[0]["state"] == "ALERT" and ev[0]["prev_state"] == "LAYING"


def test_offline_detection(env):
    client, db = env
    send(client, "IDLE", 0)
    login(client)
    assert client.get("/api/devices").json()["devices"][0]["online"] is True
    db.get_db().devices.update_one({"_id": "kria-01"}, {"$set": {"last_seen": db.now() - timedelta(seconds=30)}})
    assert client.get("/api/devices").json()["devices"][0]["online"] is False


def test_restart_keeps_data(env):
    client, db = env
    send(client, "IDLE", 0)
    db.init_db(30)            # second start-up against an existing database
    db.init_db(7)             # ...with a different retention setting
    assert db.get_db().events.count_documents({}) == 1
    assert db.get_db().events.index_information()["ttl_ts"]["expireAfterSeconds"] == 7 * 86400


def test_privacy_and_retention_indexes(env):
    client, db = env
    login(client)
    p = client.get("/api/privacy").json()
    events = next(t for t in p["schema"] if t["collection"] == "events")
    assert events["fields"] == ["_id", "device_id", "state", "prev_state", "ts"]
    assert p["accepted_device_payload"] == ["epoch", "seq", "state"]
    idx = db.get_db().events.index_information()
    assert idx["ttl_ts"]["expireAfterSeconds"] == 30 * 86400           # auto-delete after 30 days
    assert db.get_db().sessions.index_information()["ttl_expires_at"]["expireAfterSeconds"] == 0
