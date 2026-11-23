"""Bl!nd server: receives state frames from the ESP32 relay, serves the dashboard.

Device side  : POST /api/device/{device_id}/state   (Bearer device key)
Dashboard    : /api/auth/*, /api/devices, /api/events, /api/alerts, /api/stats, /api/privacy
Database     : MongoDB (see app/db.py)
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import db
from .db import epoch, get_db, now
from .security import new_token, sha256, verify_password

OFFLINE_AFTER = float(os.environ.get("BLIND_OFFLINE_AFTER", "5"))      # seconds without a frame
RETENTION_DAYS = float(os.environ.get("BLIND_RETENTION_DAYS", "30"))
SESSION_HOURS = float(os.environ.get("BLIND_SESSION_HOURS", "12"))
COOKIE_SECURE = os.environ.get("BLIND_COOKIE_SECURE", "0") == "1"      # set to 1 behind HTTPS
MAX_DEVICE_BODY = 256                                                  # bytes; a state frame is tiny

State = Literal["IDLE", "FALLING", "LAYING", "ALERT"]


@asynccontextmanager
async def lifespan(_app):
    db.init_db(RETENTION_DAYS)
    yield


app = FastAPI(title="Bl!nd server", version="0.2.0", lifespan=lifespan)


# --------------------------------------------------------------------------- device ingest

class StateFrame(BaseModel):
    """The ONLY payload the server accepts from a device. Unknown fields are rejected,
    so nothing image-like can be smuggled in through this endpoint."""
    model_config = ConfigDict(extra="forbid")
    epoch: int = Field(ge=0, le=2**31, description="Bumped on each device reboot (stored in ESP32 NVS)")
    seq: int = Field(ge=0, le=2**53, description="Frame counter, increases by 1 per frame within an epoch")
    state: State


def _device_auth(device_id: str, authorization: Optional[str]) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing device key")
    device = get_db().devices.find_one({"_id": device_id})
    if device is None or device["key_hash"] != sha256(authorization[7:]):
        raise HTTPException(401, "bad device credentials")
    return device


@app.post("/api/device/{device_id}/state")
async def ingest_state(device_id: str, request: Request, authorization: Optional[str] = Header(None)):
    body = await request.body()
    if len(body) > MAX_DEVICE_BODY:
        raise HTTPException(413, "frame too large")
    _device_auth(device_id, authorization)
    try:
        frame = StateFrame.model_validate_json(body)
    except Exception:
        raise HTTPException(422, "invalid frame")

    t = now()
    d = get_db()
    # Atomic: only applies if (epoch, seq) is newer than the last frame -> retries/replays are no-ops.
    before = d.devices.find_one_and_update(
        {"_id": device_id, "$or": [
            {"last_epoch": {"$lt": frame.epoch}},
            {"last_epoch": frame.epoch, "last_seq": {"$lt": frame.seq}},
        ]},
        {"$set": {"last_seen": t, "last_epoch": frame.epoch, "last_seq": frame.seq,
                  "current_state": frame.state}},
        return_document=ReturnDocument.BEFORE,
    )
    if before is None:
        d.devices.update_one({"_id": device_id}, {"$set": {"last_seen": t}})
        return {"ok": True, "duplicate": True}

    prev = before.get("current_state")
    changed = frame.state != prev
    if changed:  # store transitions only; repeated frames just act as a heartbeat
        d.events.insert_one({"device_id": device_id, "state": frame.state, "prev_state": prev, "ts": t})
    return {"ok": True, "duplicate": False, "transition": changed}


# --------------------------------------------------------------------------- user auth

class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def current_user(blind_session: Optional[str] = Cookie(None)) -> dict:
    if not blind_session:
        raise HTTPException(401, "not logged in")
    s = get_db().sessions.find_one({"_id": sha256(blind_session), "expires_at": {"$gt": now()}})
    if s is None:
        raise HTTPException(401, "session expired")
    return {"id": str(s["user_id"]), "username": s["username"], "_oid": s["user_id"]}


@app.post("/api/auth/login")
def login(body: Login, response: Response):
    d = get_db()
    user = d.users.find_one({"username": body.username})
    if user is None or not verify_password(body.password, user["pw_hash"]):
        raise HTTPException(401, "wrong username or password")
    token = new_token()
    d.sessions.insert_one({"_id": sha256(token), "user_id": user["_id"], "username": user["username"],
                           "expires_at": now() + timedelta(hours=SESSION_HOURS)})
    response.set_cookie("blind_session", token, httponly=True, samesite="strict",
                        secure=COOKIE_SECURE, max_age=int(SESSION_HOURS * 3600))
    return {"username": user["username"]}


@app.post("/api/auth/logout")
def logout(response: Response, blind_session: Optional[str] = Cookie(None)):
    if blind_session:
        get_db().sessions.delete_one({"_id": sha256(blind_session)})
    response.delete_cookie("blind_session")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return {"id": user["id"], "username": user["username"]}


# --------------------------------------------------------------------------- dashboard reads

def _acked_ids(d, event_ids: list) -> dict:
    return {a["_id"]: a for a in d.alert_acks.find({"_id": {"$in": event_ids}})}


def _open_alert_device_ids(d) -> set:
    alerts = list(d.events.find({"state": "ALERT"}, {"_id": 1, "device_id": 1}))
    acked = _acked_ids(d, [a["_id"] for a in alerts])
    return {a["device_id"] for a in alerts if a["_id"] not in acked}


def _device_names(d) -> dict:
    return {x["_id"]: x["name"] for x in d.devices.find({}, {"name": 1})}


@app.get("/api/devices")
def list_devices(user: dict = Depends(current_user)):
    d = get_db()
    open_ids = _open_alert_device_ids(d)
    t = now()
    out = []
    for x in d.devices.find().sort("name"):
        last = x.get("last_seen")
        out.append({
            "id": x["_id"],
            "name": x["name"],
            "current_state": x.get("current_state"),
            "last_seen": epoch(last),
            "online": last is not None and (t - last).total_seconds() <= OFFLINE_AFTER,
            "open_alert": x["_id"] in open_ids,
        })
    return {"devices": out, "offline_after_s": OFFLINE_AFTER, "server_time": epoch(t)}


@app.get("/api/events")
def list_events(user: dict = Depends(current_user),
                device_id: Optional[str] = None,
                since: Optional[float] = None,
                limit: int = Query(200, ge=1, le=2000)):
    d = get_db()
    q: dict = {}
    if device_id:
        q["device_id"] = device_id
    if since is not None:
        q["ts"] = {"$gte": datetime.fromtimestamp(since, timezone.utc)}
    names = _device_names(d)
    rows = d.events.find(q).sort([("ts", DESCENDING), ("_id", DESCENDING)]).limit(limit)
    return {"events": [{"id": str(e["_id"]), "device_id": e["device_id"],
                        "device_name": names.get(e["device_id"], e["device_id"]),
                        "state": e["state"], "prev_state": e.get("prev_state"), "ts": epoch(e["ts"])}
                       for e in rows]}


@app.get("/api/alerts")
def list_alerts(user: dict = Depends(current_user),
                status: Literal["open", "all"] = "all",
                limit: int = Query(100, ge=1, le=1000)):
    d = get_db()
    q: dict = {"state": "ALERT"}
    if status == "open":
        q["_id"] = {"$nin": [a["_id"] for a in d.alert_acks.find({}, {"_id": 1})]}
    alerts = list(d.events.find(q).sort("ts", DESCENDING).limit(limit))
    acks = _acked_ids(d, [a["_id"] for a in alerts])
    names = _device_names(d)
    out = []
    for a in alerts:
        ack = acks.get(a["_id"])
        out.append({"id": str(a["_id"]), "device_id": a["device_id"],
                    "device_name": names.get(a["device_id"], a["device_id"]), "ts": epoch(a["ts"]),
                    "acked_at": epoch(ack["acked_at"]) if ack else None,
                    "acked_by": ack["username"] if ack else None})
    return {"alerts": out}


@app.post("/api/alerts/{event_id}/ack")
def ack_alert(event_id: str, user: dict = Depends(current_user)):
    d = get_db()
    try:
        oid = ObjectId(event_id)
    except InvalidId:
        raise HTTPException(404, "alert not found")
    ev = d.events.find_one({"_id": oid}, {"state": 1})
    if ev is None or ev["state"] != "ALERT":
        raise HTTPException(404, "alert not found")
    try:
        d.alert_acks.insert_one({"_id": oid, "user_id": user["_oid"], "username": user["username"],
                                 "acked_at": now()})
    except DuplicateKeyError:
        pass  # already acknowledged
    return {"ok": True}


@app.get("/api/stats")
def stats(user: dict = Depends(current_user), hours: int = Query(24, ge=1, le=24 * 30)):
    """Possible falls and alerts per hour, for the dashboard chart."""
    t = now()
    start = t - timedelta(hours=hours)
    buckets = [{"hour_start": epoch(start) + i * 3600, "falls": 0, "alerts": 0} for i in range(hours)]
    for e in get_db().events.find({"ts": {"$gte": start}, "state": {"$in": ["FALLING", "ALERT"]}},
                                  {"state": 1, "ts": 1}):
        i = min(int((e["ts"] - start).total_seconds() // 3600), hours - 1)
        buckets[i]["falls" if e["state"] == "FALLING" else "alerts"] += 1
    return {"hours": hours, "buckets": buckets}


@app.get("/api/privacy")
def privacy(user: dict = Depends(current_user)):
    return {
        "camera_derived_fields": ["device_id", "state", "prev_state", "ts"],
        "accepted_device_payload": list(StateFrame.model_fields.keys()),
        "max_device_frame_bytes": MAX_DEVICE_BODY,
        "retention_days": RETENTION_DAYS,
        "stores_images": False,
        "enforced_by": "MongoDB $jsonSchema validator (additionalProperties: false) on every collection",
        "schema": db.data_inventory(),
    }


@app.get("/api/health")
def health():
    try:
        get_db().command("ping")
        return {"ok": True, "db": "up"}
    except Exception:
        return JSONResponse({"ok": False, "db": "down"}, status_code=503)


# --------------------------------------------------------------------------- built dashboard

WEB_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_DIST, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(os.path.join(WEB_DIST, "index.html"))
