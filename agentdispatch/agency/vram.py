"""will this local model fit on the GPUs right now? Ollama runs on the host and spreads a model over the GPUs, so the room that matters is VRAM: the weights plus the KV cache for the pinned context plus
some working buffers. This polls Ollama (installed models /api/tags, loaded models /api/ps, a model's layer / head figures /api/show) and nvidia-smi (what each GPU has free), caches the answers for a
short time, and says for a model and a context whether it fits - and if not, what smaller context would. Memory held by models Ollama has loaded counts as free (Ollama unloads idle models to make room);
memory held by anything else (renders, other programs) does not. The router asks before it picks a model; the agent's context is chosen here; the conductor sees the same numbers."""
import json, re, subprocess, threading, time, urllib.request
from ..paths import OLLAMA_URL as _OLLAMA

OVERHEAD = 1.15            # working buffers on top of weights + cache
SLACK_GB = 1.0
USABLE = 0.92              # never plan to the last byte of the GPUs
FLOOR_CTX = 32768
CTX_STEPS = (131072, 65536, 49152, 32768, 24576, 16384)


def _get(url, timeout=4, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def nvidia_smi():
    """-> [(index, used_gb, total_gb)] or [] when it cannot be read"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if __import__("os").name == "nt" else 0).stdout
        rows = []
        for line in out.strip().splitlines():
            i, u, t = [x.strip() for x in line.split(",")]
            rows.append((int(i), float(u) / 1024.0, float(t) / 1024.0))
        return rows
    except Exception:
        return []


def gpu_busy(min_pct=10):
    """is any GPU doing work right now (utilisation at or above `min_pct` %)? A local model that is still computing is alive however long it takes."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if __import__("os").name == "nt" else 0).stdout
        return any(int(x.strip()) >= min_pct for x in out.split() if x.strip().isdigit())
    except Exception:
        return False


def base_name(model):
    """qwen3-coder:30b-ctx64k (old style) or qwen3-coder-ctx64k:30b (current) -> qwen3-coder:30b (the pinned-context variants share their base's weights)"""
    return re.sub(r"-ctx\d+k(?=$|:)", "", model or "")


class VramWatch:
    def __init__(self, ollama=_OLLAMA, fetch=_get, smi=nvidia_smi, now=time.time, ttl=30):
        self.ollama, self.fetch, self.smi, self.now, self.ttl = ollama, fetch, smi, now, ttl
        self._c, self._lock = {}, threading.Lock()

    def _cached(self, key, fn, ttl=None):
        with self._lock:
            hit = self._c.get(key)
            if hit and self.ttl and self.now() - hit[0] < (ttl or self.ttl):                      # ttl 0 = never cache (tests)
                return hit[1]
        try:
            val = fn()
        except Exception:
            val = None
        with self._lock:
            self._c[key] = (self.now(), val)
        return val

    # ---------------------------------------------------------------- what is there
    def installed(self):
        d = self._cached("tags", lambda: self.fetch(self.ollama + "/api/tags"))
        return {m["name"]: m for m in (d or {}).get("models", [])}

    def loaded(self):
        d = self._cached("ps", lambda: self.fetch(self.ollama + "/api/ps"), ttl=10)
        return {m["name"]: m for m in (d or {}).get("models", [])}

    def gpus(self):
        return self._cached("smi", self.smi, ttl=10) or []

    def kv_gb(self, model, ctx):
        """the KV cache for `ctx` tokens: 2 (K and V) x layers x kv heads x head size x 2 bytes, from the model's own figures (/api/show); a rough 0.12 MB per 1k tokens per billion parameters if unknown"""
        info = self._cached("show:" + base_name(model), lambda: (self.fetch(self.ollama + "/api/show", body=dict(model=base_name(model))) or {}).get("model_info", {}), ttl=3600) or {}

        def pick(suffix):
            for k, v in info.items():
                if k.endswith(suffix) and isinstance(v, (int, float)):
                    return float(v)
            return None
        layers, heads, kvh, emb, klen = pick(".block_count"), pick(".attention.head_count"), pick(".attention.head_count_kv"), pick(".embedding_length"), pick(".attention.key_length")
        if layers and heads and (emb or klen):
            hd = klen or emb / heads
            return 2 * layers * (kvh or heads) * hd * ctx * 2 / 1e9
        size = (self.installed().get(base_name(model)) or self.installed().get(model) or {}).get("size", 20e9) / 1e9
        return size * 0.12 * ctx / 1e6 * 8

    # ---------------------------------------------------------------- the decision
    def available_gb(self):
        """-> (free GB the plan may use, free now, GB held by loaded Ollama models). Unknown GPUs -> (None, ...) : no opinion"""
        g = self.gpus()
        if not g:
            return None, None, 0.0
        free = sum(max(0.0, t - u) for _, u, t in g)
        held = sum(float(m.get("size_vram") or 0) / 1e9 for m in self.loaded().values())
        return (free + held) * USABLE - SLACK_GB, free, held

    def need_gb(self, model, ctx):
        inst = self.installed()
        size = (inst.get(model) or inst.get(base_name(model)) or {}).get("size")
        if size is None:
            return None
        return (size / 1e9 + self.kv_gb(model, ctx)) * OVERHEAD

    def fit(self, model, ctx):
        """-> (fits?, need GB, available GB). Unknown model or unreadable GPUs fit (no opinion: the caller must not block on missing data)."""
        need, (avail, free, held) = self.need_gb(model, ctx), self.available_gb()
        if need is None or avail is None:
            return True, need, avail
        live = self.loaded().get(model)                                                   # already loaded with at least this context: nothing more is needed
        if live and int(live.get("context_length") or 0) >= ctx:
            return True, 0.0, avail
        return need <= avail, need, avail

    def plan(self, model, want_ctx, floor=FLOOR_CTX):
        """-> (ok, ctx to use, need GB, available GB, why): the biggest context up to `want_ctx` that fits, never below `floor`"""
        steps = [c for c in CTX_STEPS if c <= want_ctx and c >= floor] or [want_ctx]
        if want_ctx not in steps and want_ctx >= floor:
            steps = [want_ctx] + steps
        last = (True, None, None)
        for c in sorted(set(steps), reverse=True):
            ok, need, avail = self.fit(model, c)
            last = (ok, need, avail)
            if ok:
                return True, c, need, avail, "" if c == want_ctx else "context cut to %dk to fit" % (c // 1024)
        ok, need, avail = last
        return False, steps[-1], need, avail, "needs about %.0f GB (weights + %dk context) but only %.0f GB of GPU memory is free" % (need or 0, steps[-1] // 1024, avail or 0)

    def status(self):
        g = self.gpus()
        return dict(gpus=[dict(index=i, used_gb=round(u, 1), total_gb=round(t, 1)) for i, u, t in g],
                    loaded=[dict(model=n, vram_gb=round(float(m.get("size_vram") or 0) / 1e9, 1), ctx=m.get("context_length")) for n, m in self.loaded().items()],
                    available_gb=round(self.available_gb()[0] or 0, 1), installed=len(self.installed()))
