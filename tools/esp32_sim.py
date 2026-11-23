"""Fake ESP32 relay board: sends state frames the way the real board will
(one frame per second, like the privacy guard's fixed rate).

  python tools/esp32_sim.py --device kria-01 --key <KEY> --scenario fall
  python tools/esp32_sim.py ... --scenario false_fall
  python tools/esp32_sim.py ... --scenario idle --seconds 60
  python tools/esp32_sim.py ... --scenario offline     # sends a few frames then stops

Uses only the Python standard library so it runs anywhere.
"""
import argparse
import json
import random
import time
import urllib.error
import urllib.request

# (state, seconds) sequences mirroring the FSM simulations in the report.
SCENARIOS = {
    "fall":       [("IDLE", 5), ("FALLING", 1), ("LAYING", 20), ("ALERT", 15), ("IDLE", 5)],
    "false_fall": [("IDLE", 5), ("FALLING", 2), ("IDLE", 10)],
    "recovered":  [("IDLE", 5), ("FALLING", 1), ("LAYING", 8), ("IDLE", 5)],
    "idle":       [("IDLE", 30)],
    "offline":    [("IDLE", 3)],
}


def send(url: str, key: str, frame: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(frame).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--device", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--scenario", choices=SCENARIOS, default="fall")
    p.add_argument("--seconds", type=int, help="override duration for the 'idle' scenario")
    p.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast")
    p.add_argument("--epoch", type=int, default=int(time.time()) % 2**31,
                   help="real board stores this in NVS and bumps it on reboot")
    p.add_argument("--loss", type=float, default=0.0, help="fraction of frames to drop (network test)")
    a = p.parse_args()

    steps = SCENARIOS[a.scenario]
    if a.seconds and a.scenario == "idle":
        steps = [("IDLE", a.seconds)]
    url = f"{a.server}/api/device/{a.device}/state"
    seq = 0
    for state, secs in steps:
        for _ in range(secs):
            frame = {"epoch": a.epoch, "seq": seq, "state": state}
            seq += 1
            if random.random() >= a.loss:
                try:
                    res = send(url, a.key, frame)
                    mark = "->" if res.get("transition") else "  "
                    print(f"{mark} seq={frame['seq']:<4} {state}")
                except urllib.error.HTTPError as e:
                    print(f"!! seq={frame['seq']} HTTP {e.code}: {e.read().decode()}")
                    if e.code == 401:
                        raise SystemExit("device key rejected - check --device / --key")
                except urllib.error.URLError as e:
                    print(f"!! seq={frame['seq']} server unreachable: {e.reason}")
            else:
                print(f"xx seq={frame['seq']:<4} {state} (dropped)")
            time.sleep(1 / a.speed)
    print("done" + (" (device now goes silent -> dashboard should show offline)" if a.scenario == "offline" else ""))


if __name__ == "__main__":
    main()
