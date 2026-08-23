import json
import urllib.request

BASE = "http://127.0.0.1:8000"
ORIGINAL = {
    0: (190, 90),
    1: (150, 180),
    2: (180, 160),
    3: (170, 200),
    4: (180, 0),
    5: (180, 0),
    6: (180, 0),
    7: (180, 0),
}


def post(path: str, body: dict) -> None:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5).read()


for axis, (p, i) in ORIGINAL.items():
    post(f"/api/actuators/{axis}/gain", {"p": p, "i": i, "d": 0})
    post(f"/api/actuators/{axis}/target", {"mode": "position", "value": 2048})

for axis in ORIGINAL:
    item = json.load(urllib.request.urlopen(f"{BASE}/api/actuators/{axis}", timeout=5))["item"]
    g = item["gains"]
    print(axis, item["label"], "P=", g["p"], "I=", g["i"], "D=", g["d"])
