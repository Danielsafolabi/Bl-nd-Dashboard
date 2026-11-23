"""Admin CLI (uses BLIND_MONGO_URI, default mongodb://localhost:27017).

  python manage.py add-user <username>              (prompts for password)
  python manage.py add-device <device_id> "<name>"  (prints the device key ONCE)
  python manage.py rotate-key <device_id>
  python manage.py list-devices
"""
import getpass
import os
import secrets
import sys

from pymongo.errors import DuplicateKeyError

from app import db
from app.db import get_db, now
from app.security import hash_password, sha256


def main(argv: list[str]) -> int:
    db.init_db(float(os.environ.get("BLIND_RETENTION_DAYS", "30")))
    d = get_db()
    if len(argv) < 2:
        print(__doc__); return 1
    cmd, args = argv[1], argv[2:]

    if cmd == "add-user" and len(args) >= 1:
        pw = args[1] if len(args) > 1 else getpass.getpass("Password: ")
        if len(pw) < 8:
            print("Password must be at least 8 characters."); return 1
        try:
            d.users.insert_one({"username": args[0], "pw_hash": hash_password(pw), "created_at": now()})
        except DuplicateKeyError:
            print(f"User '{args[0]}' already exists."); return 1
        print(f"User '{args[0]}' created."); return 0

    if cmd == "add-device" and len(args) == 2:
        key = secrets.token_urlsafe(32)
        try:
            d.devices.insert_one({"_id": args[0], "name": args[1], "key_hash": sha256(key),
                                  "created_at": now(), "last_seen": None, "last_epoch": -1,
                                  "last_seq": -1, "current_state": None})
        except DuplicateKeyError:
            print(f"Device '{args[0]}' already exists (use rotate-key)."); return 1
        print(f"Device '{args[0]}' registered.\nDevice key (shown once, flash it to the ESP32):\n{key}")
        return 0

    if cmd == "rotate-key" and len(args) == 1:
        key = secrets.token_urlsafe(32)
        if not d.devices.update_one({"_id": args[0]}, {"$set": {"key_hash": sha256(key)}}).matched_count:
            print("No such device."); return 1
        print(f"New key for '{args[0]}':\n{key}"); return 0

    if cmd == "list-devices":
        for x in d.devices.find({}, {"key_hash": 0}):
            print(x)
        return 0

    print(__doc__); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
