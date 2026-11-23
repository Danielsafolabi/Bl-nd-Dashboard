"""MongoDB storage for Bl!nd.

Privacy rule from the report: the only camera-derived data we keep is
(device ID, state, timestamp). The `events` collection has a $jsonSchema
validator with additionalProperties: false, so MongoDB itself refuses any
document that carries an extra field (an image, a pose, anything).

Collections
  users       login accounts (caregivers)
  sessions    login sessions; expire automatically (TTL index)
  devices     one per Kria/ESP32 pair: key hash, health, CURRENT state
  events      state changes: device_id, state, prev_state, ts  (PAST states)
              deleted automatically after the retention window (TTL index)
  alert_acks  which caregiver acknowledged which ALERT event
"""
import os
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import CollectionInvalid, OperationFailure

MONGO_URI = os.environ.get("BLIND_MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("BLIND_DB_NAME", "blind")
STATES = ["IDLE", "FALLING", "LAYING", "ALERT"]

_client = None


def get_db():
    """`BLIND_MONGO_URI=mongomock://` runs on an in-memory fake (tests / no Mongo installed)."""
    global _client
    if _client is None:
        if MONGO_URI.startswith("mongomock://"):
            import mongomock
            _client = mongomock.MongoClient(tz_aware=True)
        else:
            _client = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=5000)
    return _client[DB_NAME]


def now() -> datetime:
    return datetime.now(timezone.utc)


def epoch(dt):
    """Datetime -> Unix seconds for the JSON API (None stays None)."""
    return dt.timestamp() if dt is not None else None


# ----------------------------------------------------------------------------- schemas
_INT = {"bsonType": ["int", "long"]}
_STATE = {"enum": STATES}
_STATE_OR_NULL = {"enum": STATES + [None]}

SCHEMAS = {
    "events": {
        "bsonType": "object",
        "required": ["device_id", "state", "prev_state", "ts"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "objectId"},
            "device_id": {"bsonType": "string", "maxLength": 64},
            "state": _STATE,
            "prev_state": _STATE_OR_NULL,
            "ts": {"bsonType": "date"},
        },
    },
    "devices": {
        "bsonType": "object",
        "required": ["_id", "name", "key_hash", "created_at", "last_epoch", "last_seq"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string", "maxLength": 64},
            "name": {"bsonType": "string", "maxLength": 120},
            "key_hash": {"bsonType": "string"},
            "created_at": {"bsonType": "date"},
            "last_seen": {"bsonType": ["date", "null"]},
            "last_epoch": _INT,
            "last_seq": _INT,
            "current_state": _STATE_OR_NULL,
        },
    },
    "users": {
        "bsonType": "object",
        "required": ["username", "pw_hash", "created_at"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "objectId"},
            "username": {"bsonType": "string", "maxLength": 64},
            "pw_hash": {"bsonType": "string"},
            "created_at": {"bsonType": "date"},
        },
    },
    "sessions": {
        "bsonType": "object",
        "required": ["_id", "user_id", "username", "expires_at"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "string"},          # sha256 of the cookie token
            "user_id": {"bsonType": "objectId"},
            "username": {"bsonType": "string"},
            "expires_at": {"bsonType": "date"},
        },
    },
    "alert_acks": {
        "bsonType": "object",
        "required": ["_id", "user_id", "username", "acked_at"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "objectId"},         # = the ALERT event's _id
            "user_id": {"bsonType": "objectId"},
            "username": {"bsonType": "string"},
            "acked_at": {"bsonType": "date"},
        },
    },
}


def init_db(retention_days: float) -> None:
    db = get_db()
    for name, schema in SCHEMAS.items():
        validator = {"$jsonSchema": schema}
        try:
            db.create_collection(name, validator=validator, validationLevel="strict",
                                 validationAction="error")
        except CollectionInvalid:  # already exists -> keep the validator up to date
            try:
                db.command("collMod", name, validator=validator,
                           validationLevel="strict", validationAction="error")
            except (OperationFailure, NotImplementedError, TypeError):
                pass  # TypeError: mongomock has no collMod
        except (TypeError, NotImplementedError):  # mongomock doesn't support validators
            if name not in db.list_collection_names():
                db.create_collection(name)

    db.users.create_index("username", unique=True)
    db.events.create_index([("device_id", ASCENDING), ("ts", DESCENDING)])
    db.events.create_index([("state", ASCENDING), ("ts", DESCENDING)])
    _ttl_index(db, "events", "ts", int(retention_days * 86400))
    _ttl_index(db, "sessions", "expires_at", 0)


def _ttl_index(db, coll: str, field: str, seconds: int) -> None:
    """MongoDB deletes documents by itself once `field` is older than `seconds`."""
    name = f"ttl_{field}"
    try:
        db[coll].create_index(field, expireAfterSeconds=seconds, name=name)
    except OperationFailure:  # retention setting changed since last start -> rebuild the index
        db[coll].drop_index(name)
        db[coll].create_index(field, expireAfterSeconds=seconds, name=name)


def data_inventory() -> list[dict]:
    """Exactly the fields each collection is allowed to hold (what the validator enforces)."""
    return [{"collection": name, "fields": list(s["properties"].keys())} for name, s in SCHEMAS.items()]
