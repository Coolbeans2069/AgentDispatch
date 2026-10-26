#!/usr/bin/env python3
"""engineroom/ollamamon.py - what the local Ollama is doing, for the engine-room OLLAMA gauge, and a log to improve things later

    python -m engineroom.ollamamon      print the current reading

Where each number comes from (Ollama itself reports no live tokens/second, so this joins three sources):
  * the Ollama server log ($OLLAMA_LOG; default %LOCALAPPDATA%/Ollama/server.log on Windows, ~/.ollama/logs/server.log elsewhere): EVERY client's requests (chat / generate / embed, native and /v1) with how long each took
        -> requests per minute, and the DUTY (share of the last minute Ollama spent answering)
  * /api/ps: which models are loaded and how much VRAM they hold
  * token counts: callers report each call with record_call(...) (the AgentDispatch router does; any bot may POST /llm)
        -> tokens per second (speed of the last call, and throughput over the last minute), tokens today
Every reported call is appended to $ENGINEROOM_DATA/metrics/ollama_calls.csv (time, who, model, prompt tokens, generated tokens, ms, tokens/s) so the history can be analysed later.
The gauge scale ("max") is the fastest generation speed ever seen (kept in $ENGINEROOM_DATA/metrics/ollama_max.json, never below 30 tok/s), so the dial means "how close to the best this box has done".
"""
import csv, json, os, re, threading, time, urllib.request

DATA = os.path.expanduser(os.environ.get("ENGINEROOM_DATA") or os.path.join("~", ".agentdispatch", "engineroom"))
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
LOG = os.environ.get("OLLAMA_LOG") or (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Ollama", "server.log") if os.name == "nt" else os.path.expanduser("~/.ollama/logs/server.log"))
PATHS = ("/api/chat", "/api/generate", "/api/embed", "/api/embeddings", "/v1/chat/completions", "/v1/completions", "/v1/embeddings")
GIN = re.compile(r'\[GIN\] (\d{4})/(\d\d)/(\d\d) - (\d\d):(\d\d):(\d\d) \|\s*(\d+)\s*\|\s*([\d.]+)(µs|us|ms|s|m)?([\d.]+s)?\s*\|[^|]*\|\s*(\w+)\s+"([^"]+)"')
_lock = threading.Lock()
_st = dict(off=None, reqs=[], ps=(0, {}), calls_t=0, calls=[])


def _dir():
    d = os.path.join(os.path.expanduser(os.environ.get("ENGINEROOM_DATA") or DATA), "metrics")
    os.makedirs(d, exist_ok=True)
    return d


def _dur(num, unit, tail):
    v = float(num)
    if unit in ("µs", "us"):
        v /= 1e6
    elif unit == "ms":
        v /= 1000.0
    elif unit == "m":                                   # Go prints 1m2.5s
        v = v * 60 + (float(tail[:-1]) if tail else 0)
    return v


def _read_log():
    """new requests since the last look (the log only ever grows; start at the end so a huge old log is not re-read)"""
    try:
        size = os.path.getsize(LOG)
        if _st["off"] is None or size < _st["off"]:
            _st["off"] = max(0, size - 300000) if _st["off"] is None else 0
        with open(LOG, "rb") as f:
            f.seek(_st["off"])
            txt = f.read().decode("utf-8", "ignore")
            _st["off"] = f.tell()
    except Exception:
        return
    for m in GIN.finditer(txt):
        y, mo, d, h, mi, se, code, num, unit, tail, meth, path = m.groups()
        if meth != "POST" or path not in PATHS:
            continue
        try:
            end = time.mktime((int(y), int(mo), int(d), int(h), int(mi), int(se), 0, 0, -1))
            _st["reqs"].append((end, _dur(num, unit, tail), path, int(code)))
        except Exception:
            pass
    cut = time.time() - 3600
    _st["reqs"] = [r for r in _st["reqs"] if r[0] > cut]


def _ps():
    if time.time() - _st["ps"][0] < 4:
        return _st["ps"][1]
    out = {}
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=2) as r:
            out = json.loads(r.read())
    except Exception:
        pass
    _st["ps"] = (time.time(), out)
    return out


def _max_file():
    return os.path.join(_dir(), "ollama_max.json")


def _max_seen(update=None):
    try:
        v = float(json.load(open(_max_file())).get("tok_s", 30))
    except Exception:
        v = 30.0
    if update and update > v:
        v = update
        try:
            json.dump(dict(tok_s=round(v, 1), when=time.strftime("%Y-%m-%d %H:%M:%S")), open(_max_file(), "w"))
        except Exception:
            pass
    return max(30.0, v)


def record_call(who, model, prompt_tokens=0, gen_tokens=0, ms=0.0):
    """one finished Ollama call (any caller). Logs it and moves the gauge. ms = how long generation took (use eval_duration/1e6 when Ollama gives it)"""
    try:
        gen, ms = int(gen_tokens or 0), float(ms or 0)
        rate = gen / (ms / 1000.0) if ms > 0 and gen > 0 else 0.0
        p = os.path.join(_dir(), "ollama_calls.csv")
        new = not os.path.isfile(p)
        with _lock, open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "epoch", "who", "model", "prompt_tokens", "gen_tokens", "ms", "tok_per_s"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), round(time.time(), 2), who, model, int(prompt_tokens or 0), gen, round(ms), round(rate, 1)])
        if rate:
            _max_seen(rate)
    except Exception:
        pass


def _calls():
    """the reported calls of today (cached 2 s), read from the shared csv so every process sees every caller"""
    if time.time() - _st["calls_t"] < 2:
        return _st["calls"]
    rows = []
    try:
        p = os.path.join(_dir(), "ollama_calls.csv")
        midnight = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 600000))
            for line in f.read().decode("utf-8", "ignore").splitlines()[1:]:
                c = line.split(",")
                if len(c) >= 8 and c[1].replace(".", "", 1).isdigit() and float(c[1]) >= midnight:
                    rows.append((float(c[1]), c[2], c[3], int(c[4] or 0), int(c[5] or 0), float(c[6] or 0), float(c[7] or 0)))
    except Exception:
        pass
    _st["calls"], _st["calls_t"] = rows, time.time()
    return rows


def snapshot():
    now = time.time()
    with _lock:
        _read_log()
        reqs = list(_st["reqs"])
    minute = [r for r in reqs if r[0] > now - 60]
    duty = min(1.0, sum(r[1] for r in minute) / 60.0)                     # a request ending in the last minute with its time counted in full: a good enough busy share
    calls = _calls()
    last20 = [c for c in calls if c[0] > now - 20 and c[6] > 0]
    last60 = [c for c in calls if c[0] > now - 60]
    now_rate = (sum(c[4] for c in last20) / max(0.001, sum(c[5] for c in last20) / 1000.0)) if last20 else 0.0
    thru = sum(c[4] for c in last60) / 60.0
    ps = _ps()
    models = [dict(name=m.get("name"), vram_gb=round((m.get("size_vram") or 0) / 2**30, 1), size_gb=round((m.get("size") or 0) / 2**30, 1)) for m in ps.get("models", [])]
    mx = _max_seen()
    return dict(up=bool(ps) or bool(reqs), now_tok_s=round(now_rate, 1), thru_tok_s=round(thru, 1), max_tok_s=round(mx, 1), duty=round(duty, 2),
                req_min=len(minute), req_hour=len(reqs), loaded=models, vram_gb=round(sum(m["vram_gb"] for m in models), 1),
                gen_today=sum(c[4] for c in calls), prompt_today=sum(c[3] for c in calls), calls_today=len(calls),
                last_model=(calls[-1][2] if calls else (models[0]["name"] if models else "")))


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1))
