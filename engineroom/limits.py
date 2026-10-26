#!/usr/bin/env python3
"""engineroom/limits.py - plan usage limits of the AI agent CLIs that share this machine, for the engine-room gauges

    python -m engineroom.limits                                                       print what is known
    python -m engineroom.limits set claude 28 2030-01-01T12:00:00Z 63 2030-01-05T09:00:00Z     name, 5-hour % used + reset, weekly % used + reset
    python -m engineroom.limits set pullagent - - 40 2030-01-07T00:00:00Z                       (a weekly-only plan: '-' for the 5-hour window)

Where each number comes from (they are DIFFERENT pools - not one number):
  codex   READ LIVE by asking the Codex CLI itself (`codex app-server`, method account/rateLimits/read: costs no quota, uses Codex's own login), refreshed every 5 minutes;
          falls back to the rate-limit lines in its session files (~/.codex/sessions/.../rollout-*.jsonl).
  claude  PUSHED: this plan's 5-hour and weekly limits are only readable from inside the Claude app, so a Claude session posts them (POST /limit or `set`).
  pullagent PUSHED / TYPED: a pull agent (an app that polls the board for work) usually shows its plan limit only in its own window. Click the PULL AGENT dial and type it in.
  grokcli the same for the Grok CLI (its `grok usage` only prints tokens/cost of one session, not the plan limit).
Everything is kept in one file ($ENGINEROOM_LIMITS_FILE, default $ENGINEROOM_DATA/limits.json) so every reader sees the same entry.
A number older than STALE_MIN minutes is shown greyed ("as of ..."): it is the last value anyone reported, not live. A window whose reset time has passed shows 0 % (it has reset).
"""
import datetime, glob, json, os, re, sys, time

FILE = os.path.expanduser(os.environ.get("ENGINEROOM_LIMITS_FILE") or os.path.join(os.environ.get("ENGINEROOM_DATA") or os.path.join("~", ".agentdispatch", "engineroom"), "limits.json"))
STALE_MIN = 45
STALE_BY = {"codex": 15, "pullagent": 1440, "grokcli": 1440}      # a weekly figure typed in by hand stays good for a day; the live ones must be fresh
SPANS = {"5h": 300, "week": 10080}
TITLES = {"claude": "CLAUDE", "codex": "CODEX", "pullagent": "PULL AGENT", "grokcli": "GROK CLI"}
NAMES = ("claude", "codex", "pullagent", "grokcli")
_cache = {"mtime": 0, "data": {}, "codex": (0, None)}


def _epoch(x):
    if x in (None, "", "-"):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return datetime.datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _load():
    try:
        m = os.path.getmtime(FILE)
        if m != _cache["mtime"]:
            _cache.update(mtime=m, data=json.load(open(FILE, encoding="utf-8")))
    except Exception:
        pass
    return _cache["data"]


def set_limit(name, windows, source="pushed"):
    """windows: [{label: '5h'|'week', pct: 0-100, resets_at: epoch or ISO string}]"""
    ws = []
    for w in windows:
        pct, ra = w.get("pct"), _epoch(w.get("resets_at"))
        if pct in (None, "", "-"):
            continue
        label = w.get("label") or "5h"
        span = w.get("span_min")                                  # 0 / "unknown" = this plan's cycle length is not known: the "how far through" needle is then left out
        span = SPANS.get(label, 300) if span is None else int(span)
        ws.append(dict(label=label, pct=max(0.0, min(100.0, float(pct))), resets_at=ra, span_min=span))      # resets_at None = reset time not known
    data = dict(_load())
    data[name] = dict(windows=ws, asof=time.time(), source=source)
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, FILE)
    _cache.update(mtime=os.path.getmtime(FILE), data=data)       # keep the cache in step: two writes inside one mtime tick must not lose the first
    return data[name]


RL = re.compile(r'"primary":\{"used_percent":([\d.]+),"window_minutes":(\d+),"resets_at":(\d+)\},"secondary":\{"used_percent":([\d.]+),"window_minutes":(\d+),"resets_at":(\d+)\}')


def codex():
    """the Codex CLI's own record of its limits: the newest rate_limits line in the newest session files (cached 20 s)"""
    if time.time() - _cache["codex"][0] < 20:
        return _cache["codex"][1]
    out = None
    try:
        files = sorted(glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl")), key=lambda p: os.path.basename(p), reverse=True)[:4]
        for f in files:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 400000))
                txt = fh.read().decode("utf-8", "ignore")
            m = RL.findall(txt)
            if m:
                a, b = m[-1][:3], m[-1][3:]
                out = dict(windows=[dict(label="5h", pct=float(a[0]), span_min=int(a[1]), resets_at=float(a[2])),
                                    dict(label="week", pct=float(b[0]), span_min=int(b[1]), resets_at=float(b[2]))], asof=os.path.getmtime(f), source="codex session files")
                break
    except Exception:
        pass
    _cache["codex"] = (time.time(), out)
    return out


def _codex_exe():
    import shutil
    return os.environ.get("ENGINEROOM_CODEX_BIN") or shutil.which("codex") or ""


_live = {"busy": False}


def codex_live():
    """ask the Codex CLI's app-server for the live rate limits (no quota used) and store them; returns True when stored"""
    import subprocess, threading
    p = subprocess.Popen([_codex_exe(), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=0x08000000 if sys.platform == "win32" else 0)
    out = []

    def rd():
        for l in p.stdout:
            out.append(l.strip())
    threading.Thread(target=rd, daemon=True).start()

    def send(o):
        p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
    try:
        send({"method": "initialize", "id": 0, "params": {"clientInfo": {"name": "agentdispatch-engineroom", "title": "engine room", "version": "0.1"}}})
        t = time.time()
        while time.time() - t < 20 and not any(x.startswith('{"id":0') for x in out):
            time.sleep(0.3)
        send({"method": "initialized", "params": {}})
        send({"method": "account/rateLimits/read", "id": 1})
        t = time.time()
        while time.time() - t < 20:
            hit = next((x for x in out if x.startswith('{"id":1')), None)
            if hit:
                rl = (json.loads(hit).get("result") or {}).get("rateLimits") or {}
                ws = [dict(label=lab, pct=w["usedPercent"], resets_at=w["resetsAt"], span_min=w.get("windowDurationMins")) for lab, w in (("5h", rl.get("primary")), ("week", rl.get("secondary"))) if w]
                if ws:
                    set_limit("codex", ws, source="codex app-server (live)")
                    return True
                return False
            time.sleep(0.3)
    finally:
        try:
            p.kill()
        except Exception:
            pass
    return False


def _refresh_codex():
    if os.environ.get("ENGINEROOM_NO_LIVE_LIMITS"):                  # tests and offline runs: never start the Codex CLI
        return
    rec = _load().get("codex") or {}
    if _live["busy"] or time.time() - rec.get("asof", 0) < 240 or not _codex_exe() or not os.path.isfile(_codex_exe()):
        return
    _live["busy"] = True

    def run():
        try:
            codex_live()
        except Exception:
            pass
        finally:
            _live["busy"] = False
    import threading
    threading.Thread(target=run, daemon=True).start()


def view(rec, now, stale_min=STALE_MIN):
    ws = []
    for w in rec.get("windows", []):
        span = (w.get("span_min") or 0) * 60.0
        if w.get("resets_at") is None:
            ws.append(dict(label=w["label"], pct=w["pct"], elapsed=None, resets_in_min=None, reset=False, reset_unknown=True))
            continue
        left = w["resets_at"] - now
        reset = left <= 0
        ws.append(dict(label=w["label"], pct=0.0 if reset else w["pct"], elapsed=None if not span else (0.0 if reset else max(0.0, min(1.0, 1 - left / span))),
                       resets_in_min=None if reset else int(left // 60), reset=reset))
    age = (now - rec.get("asof", 0)) / 60.0
    return dict(windows=ws, asof=rec.get("asof"), age_min=round(age, 1), stale=age > stale_min, source=rec.get("source", ""))


def snapshot(now=None):
    now = now or time.time()
    _refresh_codex()
    data = dict(_load())
    c = codex()
    if c and (not data.get("codex") or c["asof"] >= data["codex"].get("asof", 0)):
        data["codex"] = c
    return {name: dict(title=TITLES.get(name, name.upper()), **(view(data[name], now, STALE_BY.get(name, STALE_MIN)) if data.get(name) else dict(windows=[], asof=None, age_min=None, stale=True, source="no data yet")))
            for name in NAMES}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["set"] and len(a) >= 6:
        print(json.dumps(set_limit(a[1], [dict(label="5h", pct=a[2], resets_at=a[3]), dict(label="week", pct=a[4], resets_at=a[5])]), indent=1))
    else:
        print(json.dumps(snapshot(), indent=1))
        time.sleep(0.2)
