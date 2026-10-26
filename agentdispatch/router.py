"""an OpenAI-compatible router in front of the local Ollama, OpenRouter free models, and the free Gemini tier.

    POST /v1/chat/completions   model = "auto" | "auto-local" | "auto-cloud" | an Ollama name | openrouter/... | a gemini-* name
    GET  /v1/models

"auto" tries the local model first (when a GPU has room), then OpenRouter free (when a key is set), then Gemini if the engine room's
budget allows, then a bigger local model; every call is logged and reported to the engine room. A chat model that cannot finish a task
can say so on a line starting `BOARD:` - the router posts that line on the notice board (and strips it from the answer), so even models
without tool support can use the board. If every route fails, the router posts the failure itself."""
import json, os, re, subprocess, threading, time, urllib.error, urllib.parse, urllib.request

from . import openrouter as OR
from .similarity import similarity

DEFAULTS = dict(
    ollama_url="http://127.0.0.1:11434", engine_room="http://127.0.0.1:8898", gemini_url="https://generativelanguage.googleapis.com/v1beta/openai",
    openrouter_url=OR.API_URL, openrouter_cloud=OR.DEFAULT_CLOUD,
    local_small="local-qwen3-4b", local_big="local-gemma3-27b", vision="qwen2.5vl:7b", cloud="gemini-3.5-flash-lite", cloud_big="gemini-3.8-flash",
    small_max_tokens=3000, local_concurrency=1, local_wait_s=3600, inject_board_note=True, free_vram_gb=6.0, check_vram=True, timeout_s=900, local_timeout_s=14400,
    local_ctx_min=16384, local_ctx_max=131072,                 # quality and context over speed: a local call gets a window that fits its prompt and its answer (it used to be fixed at 8192, silently cutting long prompts)
    local_tools=["local-qwen3.5-27b", "local-qwen3-4b", "qwen3:4b", "local-qwen3-30b", "qwen3.6:35b", "local-gpt-oss-20b"],
)
CTX_MIN, CTX_MAX = 16384, 131072
BOARD_NOTE = ("(Notice board) Other AI assistants share a notice board. If you cannot complete this task well - you are stuck, need a tool or knowledge you do not have, or keep failing - "
              "finish your answer with ONE line starting 'BOARD:' followed by a short title of the problem. Do not put secrets in it.")
BOARD_LINE = re.compile(r"(?mi)^\s*BOARD:\s*(.+?)\s*$")


class RouteError(Exception):
    def __init__(self, status, message, tried=None):
        super().__init__(message)
        self.status, self.tried = status, tried or []


def _post(url, body, headers=None, timeout=300):
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"), dict({"Content-Type": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def est_tokens(messages):
    n = 0
    for m in messages:
        c = m.get("content")
        n += len(c if isinstance(c, str) else json.dumps(c)) // 4 + 4
    return n


def has_images(messages):
    return any(isinstance(m.get("content"), list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"]) for m in messages)


def _args_obj(v):
    if isinstance(v, dict):
        return v
    try:
        o = json.loads(v or "{}")
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


def to_ollama(model, req, messages, stream=False):
    """OpenAI chat request -> Ollama /api/chat body (text, images, tools, tool calls and tool results)"""
    out, names = [], {}
    for m in messages:
        c = m.get("content")
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                names[tc.get("id")] = fn.get("name")
                calls.append(dict(function=dict(name=fn.get("name"), arguments=_args_obj(fn.get("arguments")))))
            out.append(dict(role="assistant", content=c if isinstance(c, str) else "", tool_calls=calls))
            continue
        if role in ("tool", "function"):
            msg = dict(role="tool", content=c if isinstance(c, str) else json.dumps(c))
            nm = m.get("name") or names.get(m.get("tool_call_id"))
            if nm:
                msg["tool_name"] = nm
            out.append(msg)
            continue
        if isinstance(c, list):
            text, imgs = [], []
            for p in c:
                if p.get("type") == "text":
                    text.append(p.get("text", ""))
                elif p.get("type") == "image_url":
                    u = (p.get("image_url") or {}).get("url", "")
                    if u.startswith("data:") and "," in u:
                        imgs.append(u.split(",", 1)[1])
            msg = dict(role=role, content="\n".join(text))
            if imgs:
                msg["images"] = imgs
            out.append(msg)
        else:
            out.append(dict(role=role, content=c or ""))
    opts = {}
    if req.get("temperature") is not None:
        opts["temperature"] = req["temperature"]
    mt = req.get("max_tokens") or req.get("max_completion_tokens")
    if mt:
        opts["num_predict"] = int(mt)
    if req.get("stop"):
        opts["stop"] = [req["stop"]] if isinstance(req["stop"], str) else list(req["stop"])[:4]
    need = int((est_tokens(messages) + (int(mt) if mt else 8192)) * 1.15) + 512
    ctx = CTX_MIN
    while ctx < need and ctx < CTX_MAX:                                                  # 16k, 32k, 64k, 128k: the smallest window that holds the prompt and the answer
        ctx *= 2
    opts["num_ctx"] = min(ctx, CTX_MAX)
    body = dict(model=model, messages=out, stream=stream, think=False, keep_alive="30m", options=opts)
    if req.get("tools"):
        body["tools"] = [t for t in req["tools"] if isinstance(t, dict) and t.get("type", "function") == "function"]
    if (req.get("response_format") or {}).get("type") in ("json_object", "json_schema"):
        body["format"] = "json"
    return body


def _openai_calls(tool_calls):
    return [dict(id="call_%d_%d" % (int(time.time() * 1000) % 10 ** 8, i), type="function",
                 function=dict(name=(tc.get("function") or {}).get("name", ""), arguments=json.dumps((tc.get("function") or {}).get("arguments") or {}, ensure_ascii=False)))
            for i, tc in enumerate(tool_calls or [])]


def from_ollama(model, resp):
    msg = resp.get("message") or {}
    text = re.sub(r"(?s)<think>.*?</think>", "", msg.get("content") or "").strip()
    calls = _openai_calls(msg.get("tool_calls"))
    pt, ct = resp.get("prompt_eval_count", 0), resp.get("eval_count", 0)
    m = dict(role="assistant", content=text if (text or not calls) else None)
    if calls:
        m["tool_calls"] = calls
    return dict(id="chatcmpl-sb%d" % int(time.time() * 1000), object="chat.completion", created=int(time.time()), model=model,
                choices=[dict(index=0, message=m, finish_reason="tool_calls" if calls else ("stop" if resp.get("done_reason", "stop") == "stop" else "length"))],
                usage=dict(prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct)), resp.get("eval_duration", 0) / 1e6


def _chain(first, rest):
    yield first
    yield from rest


class BoardFilter:
    """for streamed answers: a line that starts with 'BOARD:' is a request to the notice board, not part of the answer, so it is held back and recorded. Everything else is passed through AT ONCE
    (only the first few characters of a line are held, just long enough to tell whether it begins with 'BOARD:')."""
    TAG = "board:"

    def __init__(self):
        self.mode, self.pend, self.line, self.board = "start", "", "", []

    def feed(self, text):
        out = []
        for ch in text:
            if self.mode == "pass":
                out.append(ch)
                if ch == "\n":
                    self.mode = "start"
            elif self.mode == "board":
                if ch == "\n":
                    self._take()
                else:
                    self.line += ch
            else:                                                       # start of a line: decide
                self.pend += ch
                core = self.pend.lstrip().lower()
                if ch == "\n":
                    out.append(self.pend)
                    self.pend = ""
                elif core == "" or self.TAG.startswith(core):
                    if core == self.TAG:
                        self.mode, self.line, self.pend = "board", "", ""
                elif core.startswith(self.TAG):
                    self.mode, self.line, self.pend = "board", self.pend.lstrip()[len(self.TAG):], ""
                else:
                    out.append(self.pend)
                    self.pend, self.mode = "", "pass"
        return "".join(out)

    def _take(self):
        t = self.line.strip()
        if t:
            self.board.append(t)
        self.mode, self.line = "start", ""

    def flush(self):
        if self.mode == "board":
            self._take()
            return ""
        rest, self.pend = self.pend, ""
        return rest


class Router:
    def __init__(self, app, config=None):
        self.app = app
        cfg = dict(DEFAULTS)
        try:
            cfg.update(json.load(open(os.path.join(app.data, "router.json"), encoding="utf-8")))
        except Exception:
            pass
        cfg.update(config or {})
        self.cfg = cfg
        self.sem = threading.Semaphore(max(1, int(cfg["local_concurrency"])))
        self.log_path = os.path.join(app.data, "router.jsonl")
        self.log_lock = threading.Lock()
        self._fail_seen = {}
        self._vram = (0.0, None)
        self._tools_cap = {}

    # ------------------------------------------------------------ what is available
    def ollama_models(self):
        try:
            return {m["name"]: m.get("size", 0) for m in _get(self.cfg["ollama_url"] + "/api/tags", 5).get("models", [])}
        except Exception:
            return {}

    def supports_tools(self, model):
        """does this Ollama model advertise the 'tools' capability (cached)"""
        if model in self._tools_cap:
            return self._tools_cap[model]
        try:
            ok = "tools" in (_post(self.cfg["ollama_url"] + "/api/show", dict(model=model), timeout=10).get("capabilities") or [])
        except Exception:
            return False                                              # Ollama did not answer: not proof the model lacks tools, so do not remember it
        self._tools_cap[model] = ok
        return ok

    def tools_model(self, have):
        for name in self.cfg["local_tools"]:
            r = self.resolve_local(name, have)
            if r and self.supports_tools(r):
                return r
        for name in sorted(have):
            if re.search(r"qwen3|qwen2\.5|llama3|mistral|gpt-oss", name) and self.supports_tools(name):
                return name
        return None

    def resolve_local(self, name, have):
        for cand in (name, name + ":latest"):
            if cand in have:
                return cand
        return None

    def local_room(self, model, have):
        """is it sensible to start this local model now: already loaded, or some GPU has room for it"""
        try:
            if any(m.get("name") == model for m in _get(self.cfg["ollama_url"] + "/api/ps", 3).get("models", [])):
                return True
        except Exception:
            return False
        need = max(self.cfg["free_vram_gb"], have.get(model, 0) / 2 ** 30 * 1.15)
        free = self.free_vram()
        return True if free is None else free >= need                # no nvidia-smi: let Ollama decide (CPU or whatever it has)

    def free_vram(self, ttl=5.0):
        """the most free VRAM on any one GPU (GB), cached for a few seconds: nvidia-smi can take seconds while the cards are busy"""
        t = time.time()
        if t - self._vram[0] < ttl:
            return self._vram[1]
        val = None
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8,
                                 creationflags=0x08000000 if os.name == "nt" else 0).stdout
            vals = [(float(a) - float(b)) / 1024 for a, b in (l.split(",") for l in out.splitlines() if l.strip())]
            val = max(vals) if vals else None
        except Exception:
            val = None
        self._vram = (t, val)
        return val

    def _or_key(self):
        return OR.api_key(self.app.data)

    def cloud_ok(self, model, backend="gemini"):
        """True when the named cloud backend may be called now (key present + engine-room budget, if reachable)"""
        if backend == "openrouter":
            if not self._or_key():
                return False
        elif not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            return False
        try:
            q = urllib.parse.quote(model, safe="")
            return bool(_get("%s/budget?model=%s&backend=%s" % (self.cfg["engine_room"], q, backend), 3).get("ok_to_send"))
        except Exception:
            return True                                               # engine room not running: the free tier's own limits still apply

    def models(self):
        have = self.ollama_models()
        ids = ["auto", "auto-local", "auto-cloud"] + sorted(have)
        if self._or_key():
            ids += OR.IDS
        ids += [self.cfg["cloud"], self.cfg["cloud_big"]]
        return dict(object="list", data=[dict(id=i, object="model", owned_by="agentdispatch") for i in dict.fromkeys(ids)])

    def _dispatch(self, backend, model, req, messages, why, stream=False):
        if backend == "ollama":
            return self._open_ollama(model, req, messages, why == "explicit") if stream else self._call_ollama(model, req, messages, why == "explicit")
        if backend == "openrouter":
            return self._open_openrouter(model, req, messages) if stream else self._call_openrouter(model, req, messages)
        return self._open_gemini(model, req, messages) if stream else self._call_gemini(model, req, messages)

    # ------------------------------------------------------------ routing
    def ladder(self, req, messages):
        """ordered list of (backend, model, why) to try"""
        want = str(req.get("model") or "auto")
        have = self.ollama_models()
        c = self.cfg
        local = lambda m, why: ("ollama", self.resolve_local(m, have) or m, why)
        or_cloud = ("openrouter", c["openrouter_cloud"], "openrouter free")
        if want not in ("auto", "auto-local", "auto-cloud"):
            if OR.is_openrouter_id(want):
                return [("openrouter", want, "explicit")]
            if want.startswith(("gemini", "models/gemini")):
                return [("gemini", want.replace("models/", ""), "explicit")]
            r = self.resolve_local(want, have)
            if r:
                if req.get("tools") and not self.supports_tools(r):
                    raise RouteError(400, "%s does not support tools; choose another model or auto" % r)
                return [("ollama", r, "explicit")]
            raise RouteError(404, "unknown model %r (see /v1/models)" % want)
        if req.get("tools"):
            tm = self.tools_model(have)
            steps = []
            if want != "auto-local":
                if self._or_key():
                    steps.append(("openrouter", c["openrouter_cloud"], "function calling: openrouter free first"))
                steps.append(("gemini", c["cloud"], "function calling: the cloud model"))
            if tm:
                steps.append(("ollama", tm, "function calling (local, advertises tools)"))
            if not steps:
                raise RouteError(400, "no installed local model supports tools and cloud is not selected")
            return steps
        if has_images(messages):
            steps = [local(c["vision"], "images")]
            if self._or_key():
                steps.append(("openrouter", "nvidia/nemotron-3.5-super-vl:free", "images via openrouter"))
            steps.append(("gemini", c["cloud"], "images"))
            return steps
        small_ok = est_tokens(messages) <= c["small_max_tokens"]
        steps = []
        if want == "auto-local":
            steps = [local(c["local_small"], "local only"), local(c["local_big"], "local only (bigger)")]
        elif want == "auto-cloud":
            if self._or_key():
                steps.append(or_cloud)
            steps += [("gemini", c["cloud"], "gemini free"), local(c["local_small"], "cloud unavailable")]
        else:
            if small_ok:
                steps.append(local(c["local_small"], "short task: local first"))
            if self._or_key():
                steps.append(("openrouter", c["openrouter_cloud"], "openrouter free tier" if small_ok else "long prompt: openrouter free first"))
            steps += [("gemini", c["cloud"], "gemini free tier" if small_ok else "long prompt: the cloud context"), local(c["local_big"], "bigger local model")]
        return steps

    def _call_ollama(self, model, req, messages, explicit=False):
        have = self.ollama_models()
        if model not in have:
            raise RouteError(404, "model %s not installed" % model)
        if self.cfg["check_vram"] and not explicit:                                               # an explicitly requested model is the caller's decision; "auto" is polite about the GPUs: it WAITS for room instead of failing
            deadline = time.time() + self.cfg["local_wait_s"]
            while not self.local_room(model, have):
                if time.time() >= deadline:
                    raise RouteError(503, "no GPU has room for %s right now (waited %d s)" % (model, self.cfg["local_wait_s"]))
                time.sleep(15)
        if not self.sem.acquire(timeout=self.cfg["local_wait_s"]):
            raise RouteError(503, "the local model is busy with another request")
        try:
            raw = _post(self.cfg["ollama_url"] + "/api/chat", to_ollama(model, req, messages), timeout=self.cfg["local_timeout_s"])
        finally:
            self.sem.release()
        resp, ms = from_ollama(model, raw)
        try:
            _post(self.cfg["engine_room"] + "/llm", dict(backend="ollama", who="agentdispatch-router", model=model, prompt_tokens=resp["usage"]["prompt_tokens"],
                                                         gen_tokens=resp["usage"]["completion_tokens"], ms=ms), timeout=2)
        except Exception:
            pass
        return resp

    def _call_gemini(self, model, req, messages):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RouteError(503, "no Gemini key")
        if not self.cloud_ok(model, "gemini"):
            raise RouteError(429, "the engine room's free-tier budget for %s is used up for now" % model)
        body = {k: v for k, v in req.items() if k in ("temperature", "max_tokens", "max_completion_tokens", "response_format", "top_p", "stop", "tools", "tool_choice", "parallel_tool_calls", "seed", "presence_penalty", "frequency_penalty")}
        body.update(model=model, messages=messages)
        try:
            resp = _post(self.cfg["gemini_url"] + "/chat/completions", body, {"Authorization": "Bearer " + key}, self.cfg["timeout_s"])
        except urllib.error.HTTPError as e:
            raise RouteError(e.code, "gemini: HTTP %d" % e.code)                  # a 429 is NOT retried here: the ladder moves on
        try:
            u = resp.get("usage") or {}
            _post(self.cfg["engine_room"] + "/llm", dict(backend="gemini", who="agentdispatch-router", model=model, tokens=u.get("total_tokens", 0)), timeout=2)
        except Exception:
            pass
        return resp

    def _call_openrouter(self, model, req, messages):
        key = self._or_key()
        if not key:
            raise RouteError(503, "no OpenRouter key (set OPENROUTER_API_KEY)")
        if not self.cloud_ok(model, "openrouter"):
            raise RouteError(429, "the engine room's OpenRouter free-tier budget is used up for now")
        body = {k: v for k, v in req.items() if k in ("temperature", "max_tokens", "max_completion_tokens", "response_format", "top_p", "stop", "tools", "tool_choice", "parallel_tool_calls", "seed", "presence_penalty", "frequency_penalty")}
        body.update(model=model, messages=messages)
        try:
            resp = _post(self.cfg["openrouter_url"] + "/chat/completions", body, OR.headers(key), self.cfg["timeout_s"])
        except urllib.error.HTTPError as e:
            raise RouteError(e.code, "openrouter: HTTP %d" % e.code)
        try:
            u = resp.get("usage") or {}
            _post(self.cfg["engine_room"] + "/llm", dict(backend="openrouter", who="agentdispatch-router", model=model, tokens=u.get("total_tokens", 0)), timeout=2)
        except Exception:
            pass
        return resp

    def _prepare(self, req):
        """validate the request, add the notice-board note for auto* models; -> (messages, inject, asked_model)"""
        if not isinstance(req, dict):
            raise RouteError(400, "the request must be a JSON object")
        if req.get("functions") or req.get("function_call"):
            raise RouteError(400, "the legacy functions/function_call parameters are not supported: use tools/tool_choice")
        if int(req.get("n") or 1) != 1:
            raise RouteError(400, "n > 1 is not supported")
        messages = [dict(m) for m in (req.get("messages") or [])]
        if not messages:
            raise RouteError(400, "messages is required")
        want = str(req.get("model") or "auto")
        inject = bool(self.cfg["inject_board_note"] and want.startswith("auto") and not (req.get("agentdispatch") or {}).get("no_board"))
        if inject:
            if messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
                messages[0]["content"] += "\n\n" + BOARD_NOTE
            else:
                messages.insert(0, dict(role="system", content=BOARD_NOTE))
        return messages, inject, want

    def chat(self, req, actor="unknown"):
        t0 = time.time()
        messages, inject, want = self._prepare(req)
        tried = []
        resp = None
        for backend, model, why in self.ladder(req, messages):
            try:
                resp = self._dispatch(backend, model, req, messages, why, stream=False)
                tried.append(dict(backend=backend, model=model, why=why, ok=True))
                break
            except RouteError as e:
                tried.append(dict(backend=backend, model=model, why=why, ok=False, error=str(e)[:120]))
            except Exception as e:
                tried.append(dict(backend=backend, model=model, why=why, ok=False, error=("%s: %s" % (e.__class__.__name__, e))[:120]))
        if resp is None:
            self._log(actor, want, tried, 0, time.time() - t0, False)
            self._report_failure(actor, want, tried)
            raise RouteError(502, "no backend could answer: " + "; ".join("%s/%s: %s" % (t["backend"], t["model"], t.get("error")) for t in tried), tried)
        text = resp["choices"][0]["message"].get("content") or ""
        posted = []
        for m in (BOARD_LINE.finditer(text) if inject else ()):                 # only when the note was injected: a model that merely quotes a "BOARD:" line is not asking for a post
            r = self._post_board(actor, m.group(1), tried[-1])
            if r:
                posted.append(r)
        if inject and BOARD_LINE.search(text):
            resp["choices"][0]["message"]["content"] = BOARD_LINE.sub("", text).rstrip()
        resp["agentdispatch"] = dict(route=tried, board_posts=posted)
        self._log(actor, want, tried, (resp.get("usage") or {}).get("total_tokens", 0), time.time() - t0, True)
        return resp

    # ------------------------------------------------------------ streaming
    def chat_stream(self, req, actor="unknown"):
        """a generator of OpenAI-style SSE strings. Backends are tried in ladder order until one OPENS; after the first byte the stream is committed. RouteError is raised (before any output) if none opens."""
        t0 = time.time()
        messages, inject, want = self._prepare(req)
        tried = []
        for backend, model, why in self.ladder(req, messages):
            try:
                src = self._dispatch(backend, model, req, messages, why, stream=True)
                first = next(src, None)
                if first is None:
                    raise RouteError(502, "empty stream")
                tried.append(dict(backend=backend, model=model, why=why, ok=True))
                break
            except RouteError as e:
                tried.append(dict(backend=backend, model=model, why=why, ok=False, error=str(e)[:120]))
            except Exception as e:
                tried.append(dict(backend=backend, model=model, why=why, ok=False, error=("%s: %s" % (e.__class__.__name__, e))[:120]))
        else:
            self._log(actor, want, tried, 0, time.time() - t0, False)
            self._report_failure(actor, want, tried)
            raise RouteError(502, "no backend could answer: " + "; ".join("%s/%s: %s" % (t["backend"], t["model"], t.get("error")) for t in tried), tried)
        return self._pump(first, src, backend, model, tried, inject, want, actor, t0)

    def _pump(self, first, src, backend, model, tried, inject, want, actor, t0):
        filt = BoardFilter() if inject else None
        posted = []
        rid, created, usage = "chatcmpl-sb%d" % int(time.time() * 1000), int(time.time()), None
        base = dict(id=rid, object="chat.completion.chunk", created=created, model=model)
        chunk = lambda delta, fin=None, extra=None: "data: " + json.dumps(dict(base, choices=[dict(index=0, delta=delta, finish_reason=fin)], **(extra or {}))) + "\n\n"
        yield chunk(dict(role="assistant", content=""))
        finish, ok, n_calls = "stop", True, 0
        try:
            for ev in _chain(first, src):
                if ev.get("text"):
                    t = filt.feed(ev["text"]) if filt else ev["text"]
                    if t:
                        yield chunk(dict(content=t))
                if ev.get("tool_calls"):
                    calls = []
                    for c in ev["tool_calls"]:
                        if "index" not in c:                              # Ollama sends each call whole, possibly in separate chunks: number them across the whole stream
                            c = dict(c, index=n_calls)
                            n_calls += 1
                        calls.append(c)
                    yield chunk(dict(tool_calls=calls))
                    finish = "tool_calls"
                if ev.get("finish"):
                    finish = "tool_calls" if finish == "tool_calls" else ev["finish"]
                if ev.get("usage"):
                    usage = ev["usage"]
        except Exception as e:
            ok = False
            yield chunk(dict(content="\n[router: the stream broke: %s]" % str(e)[:80]), "stop")
        if filt:
            rest = filt.flush()
            if rest:
                yield chunk(dict(content=rest))
            posted = [r for r in (self._post_board(actor, t, tried[-1]) for t in filt.board) if r]
        yield chunk({}, finish, dict(usage=usage or dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)))
        yield "data: [DONE]\n\n"
        self._log(actor, want, tried, (usage or {}).get("total_tokens", 0), time.time() - t0, ok, board=posted)

    def _open_ollama(self, model, req, messages, explicit=False):
        have = self.ollama_models()
        if model not in have:
            raise RouteError(404, "model %s not installed" % model)
        if self.cfg["check_vram"] and not explicit and not self.local_room(model, have):
            raise RouteError(503, "no GPU has room for %s right now" % model)
        body = to_ollama(model, req, messages, stream=True)
        http = urllib.request.Request(self.cfg["ollama_url"] + "/api/chat", json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        if not self.sem.acquire(timeout=self.cfg["local_wait_s"]):
            raise RouteError(503, "the local model is busy with another request")
        try:
            resp = urllib.request.urlopen(http, timeout=self.cfg["timeout_s"])
        except Exception:
            self.sem.release()
            raise

        def gen():
            ms = 0.0
            try:
                for line in resp:
                    line = line.strip()
                    if not line:
                        continue
                    o = json.loads(line)
                    msg = o.get("message") or {}
                    ev = {}
                    if msg.get("content"):
                        ev["text"] = msg["content"]
                    if msg.get("tool_calls"):
                        ev["tool_calls"] = _openai_calls(msg["tool_calls"])
                    if o.get("done"):
                        pt, ct = o.get("prompt_eval_count", 0), o.get("eval_count", 0)
                        ev["usage"] = dict(prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct)
                        ev["finish"] = "stop" if o.get("done_reason", "stop") == "stop" else "length"
                        ms = (o.get("eval_duration") or 0) / 1e6
                    if ev:
                        yield ev
                try:
                    _post(self.cfg["engine_room"] + "/llm", dict(backend="ollama", who="agentdispatch-router", model=model, prompt_tokens=pt, gen_tokens=ct, ms=ms), timeout=2)
                except Exception:
                    pass
            finally:
                resp.close()
                self.sem.release()
        return gen()

    def _open_gemini(self, model, req, messages):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RouteError(503, "no Gemini key")
        if not self.cloud_ok(model, "gemini"):
            raise RouteError(429, "the engine room's free-tier budget for %s is used up for now" % model)
        body = {k: v for k, v in req.items() if k in ("temperature", "max_tokens", "max_completion_tokens", "response_format", "top_p", "stop", "tools", "tool_choice", "parallel_tool_calls", "seed")}
        body.update(model=model, messages=messages, stream=True, stream_options=dict(include_usage=True))
        http = urllib.request.Request(self.cfg["gemini_url"] + "/chat/completions", json.dumps(body).encode("utf-8"), {"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            resp = urllib.request.urlopen(http, timeout=self.cfg["timeout_s"])
        except urllib.error.HTTPError as e:
            raise RouteError(e.code, "gemini: HTTP %d" % e.code)
        return self._sse_gen(resp, "gemini", model)

    def _open_openrouter(self, model, req, messages):
        key = self._or_key()
        if not key:
            raise RouteError(503, "no OpenRouter key (set OPENROUTER_API_KEY)")
        if not self.cloud_ok(model, "openrouter"):
            raise RouteError(429, "the engine room's OpenRouter free-tier budget is used up for now")
        body = {k: v for k, v in req.items() if k in ("temperature", "max_tokens", "max_completion_tokens", "response_format", "top_p", "stop", "tools", "tool_choice", "parallel_tool_calls", "seed")}
        body.update(model=model, messages=messages, stream=True, stream_options=dict(include_usage=True))
        http = urllib.request.Request(self.cfg["openrouter_url"] + "/chat/completions", json.dumps(body).encode("utf-8"), OR.headers(key))
        try:
            resp = urllib.request.urlopen(http, timeout=self.cfg["timeout_s"])
        except urllib.error.HTTPError as e:
            raise RouteError(e.code, "openrouter: HTTP %d" % e.code)
        return self._sse_gen(resp, "openrouter", model)

    def _sse_gen(self, resp, backend, model):
        """shared OpenAI SSE reader for Gemini and OpenRouter streams"""
        def gen():
            tokens = 0
            try:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    o = json.loads(data)
                    ev = {}
                    for ch in o.get("choices") or []:
                        d = ch.get("delta") or {}
                        if d.get("content"):
                            ev["text"] = d["content"]
                        if d.get("tool_calls"):
                            ev["tool_calls"] = [dict(index=tc.get("index", i), **({"id": tc["id"]} if tc.get("id") else {}), type="function", function=tc.get("function") or {}) for i, tc in enumerate(d["tool_calls"])]
                        if ch.get("finish_reason"):
                            ev["finish"] = ch["finish_reason"]
                    if o.get("usage"):
                        ev["usage"] = o["usage"]
                        tokens = o["usage"].get("total_tokens", 0)
                    if ev:
                        yield ev
                try:
                    _post(self.cfg["engine_room"] + "/llm", dict(backend=backend, who="agentdispatch-router", model=model, tokens=tokens), timeout=2)
                except Exception:
                    pass
            finally:
                resp.close()
        return gen()

    # ------------------------------------------------------------ board and log
    def _post_board(self, actor, title, via):
        board = self.app.board
        title = title[:120]
        try:
            dup = [h for h in board.search(title, 8)["results"] if h["type"] == "post" and h.get("state") in ("open", "claimed", "investigating", "escalated") and similarity(h["title"], title) >= 0.7]
            if dup:
                return dict(id=dup[0]["id"], duplicate=True)
            r = board.post("%s via router" % actor, "issue", title, "Raised by a model through the router (%s/%s) because it could not finish the task." % (via["backend"], via["model"]), ["router", "auto-posted"])
            return dict(id=r["id"])
        except Exception:
            return None

    def _report_failure(self, actor, want, tried):
        sig = "%s|%s" % (want, ";".join(sorted({t.get("error", "")[:30] for t in tried})))
        if time.time() - self._fail_seen.get(sig, 0) < 600:
            return
        self._fail_seen[sig] = time.time()
        try:
            self.app.board.post("router", "issue", "router: no backend could answer a %s request" % want, "Tried: " + "; ".join("%s/%s -> %s" % (t["backend"], t["model"], t.get("error")) for t in tried), ["router", "outage"], severity=2)
        except Exception:
            pass

    def _log(self, actor, want, tried, tokens, secs, ok, board=None):
        rec = dict(ts=round(time.time(), 2), actor=actor, asked=want, tried=tried, tokens=tokens, ms=round(secs * 1000), ok=ok)
        if board:
            rec["board_posts"] = board
        with self.log_lock, open(self.log_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
