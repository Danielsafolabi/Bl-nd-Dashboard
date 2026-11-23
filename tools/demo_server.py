"""Demo server with NO MongoDB install: in-memory database, pre-made login and devices.
Data disappears when it stops. For the real thing, use docker compose + uvicorn (see README).

  python tools/demo_server.py
  -> login: nurse / testpass123
  -> device keys: key-kria-01, key-kria-02
"""
import os, sys
os.environ["BLIND_MONGO_URI"]="mongomock://"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from app import db, main
from app.security import hash_password, sha256
db.init_db(30)
d=db.get_db()
d.users.insert_one({"username":"nurse","pw_hash":hash_password("testpass123"),"created_at":db.now()})
for i,n in [("kria-01","Room 204 bathroom"),("kria-02","Room 112 bedroom")]:
    d.devices.insert_one({"_id":i,"name":n,"key_hash":sha256("key-"+i),"created_at":db.now(),"last_seen":None,"last_epoch":-1,"last_seq":-1,"current_state":None})
import uvicorn; uvicorn.run(main.app, port=8000, log_level="warning")
