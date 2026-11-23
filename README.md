# Bl!nd: server, MongoDB database and dashboard

The receiving end of the pipeline:

```
Kria (FSM) -> UART -> ESP32-S3 relay -> Wi-Fi -> [ server + MongoDB ] -> Cloudscape dashboard
```

The server only accepts `{epoch, seq, state}` from a device and only stores
`device_id, state, prev_state, ts`. MongoDB schema validators refuse any extra field,
so there is nowhere for an image to go.

## Quick demo (no MongoDB needed)

```bash
cd server && pip install -r requirements.txt && cd ..
cd web && npm install && npm run build && cd ..
python tools/demo_server.py                     # open http://localhost:8000  (nurse / testpass123)
python tools/esp32_sim.py --device kria-01 --key key-kria-01 --scenario fall
```

Uses an in-memory database that disappears when the server stops.

## Real setup

```bash
# 1. MongoDB (needs Docker Desktop). Or use a MongoDB Atlas cluster and set BLIND_MONGO_URI.
docker compose up -d

# 2. Server
cd server
pip install -r requirements.txt
python manage.py add-user nurse                           # prompts for a password (8+ chars)
python manage.py add-device kria-01 "Room 204 bathroom"   # prints the device key ONCE, copy it
uvicorn app.main:app --port 8000

# 3. Dashboard (second terminal; or `npm run build` and the server serves it on :8000)
cd web && npm install && npm run dev                      # http://localhost:5173

# 4. Fake ESP32 (third terminal)
python tools/esp32_sim.py --device kria-01 --key <KEY> --scenario fall
```

Scenarios: `fall`, `false_fall`, `recovered`, `idle`, `offline`. Add `--speed 5` to
run faster and `--loss 0.2` to drop 20% of frames.

Tests: `cd server && python -m pytest` (run on an in-memory fake, no MongoDB needed).

## Database (MongoDB, database name `blind`)

| Collection | What it holds | Fields |
|---|---|---|
| `devices` | one per Kria/ESP32, **current state** + health | `_id` (device ID), `name`, `key_hash`, `created_at`, `last_seen`, `last_epoch`, `last_seq`, `current_state` |
| `events` | **past states**: one document per state change | `_id`, `device_id`, `state`, `prev_state`, `ts` |
| `users` | caregiver logins | `_id`, `username` (unique), `pw_hash` (scrypt), `created_at` |
| `sessions` | login sessions | `_id` (token hash), `user_id`, `username`, `expires_at` |
| `alert_acks` | who acknowledged which alert | `_id` (= ALERT event id), `user_id`, `username`, `acked_at` |

- Every collection has a `$jsonSchema` validator with `additionalProperties: false`.
- `events` has a TTL index: MongoDB deletes state history older than `BLIND_RETENTION_DAYS` by itself.
  `sessions` expire the same way.
- Times are stored as UTC dates; the API returns them as Unix seconds.

## Device API (the contract for the ESP32 firmware)

```
POST /api/device/{device_id}/state
Authorization: Bearer <device key>
Content-Type: application/json

{"epoch": 7, "seq": 1234, "state": "IDLE"}      # state: IDLE | FALLING | LAYING | ALERT
```

- Send one frame per second, matching the privacy guard's fixed rate. Repeated frames
  count as a heartbeat. Only state *changes* become documents in `events`.
- `seq` goes up by 1 per frame. `epoch` goes up by 1 on every ESP32 reboot (keep it in NVS).
  A frame whose `(epoch, seq)` isn't newer than the last one is ignored, so **retries are safe**
  and replays are dropped. The check and update happen in one atomic MongoDB operation.
- Unknown fields, unknown states, or a body over 256 bytes are rejected (422/413).
- No frame for 5 s means the device shows as **offline** (`BLIND_OFFLINE_AFTER`).
- Responses: `200 {"ok":true,"duplicate":false,"transition":true}`; `401` bad key.

## Config (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `BLIND_MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string (`mongomock://` = in-memory) |
| `BLIND_DB_NAME` | `blind` | database name |
| `BLIND_OFFLINE_AFTER` | `5` | seconds before a device is shown offline |
| `BLIND_RETENTION_DAYS` | `30` | state history older than this is deleted |
| `BLIND_SESSION_HOURS` | `12` | login session length |
| `BLIND_COOKIE_SECURE` | `0` | set to `1` once served over HTTPS |

## Not done yet

- **Not yet run against a real MongoDB server**, only the in-memory fake. On the first
  `docker compose up`, check that the server starts and that `db.getCollectionInfos()` in
  `mongosh` shows the validators. The fake doesn't enforce validators, so test once that
  inserting an extra field into `events` is refused.
- **Run behind HTTPS** before the ESP32 sends real data. Without HTTPS the device key travels
  in clear text over Wi-Fi.
- If you use MongoDB Atlas, turn on authentication and IP allow-listing; the Docker setup only
  listens on localhost.
- No login rate limiting, no user roles yet.
- The privacy guard's SipHash signature is not checked server-side yet.
