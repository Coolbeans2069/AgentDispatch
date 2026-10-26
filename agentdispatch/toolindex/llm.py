"""the language models that write tool hints for the index: Gemma 4 on the free Gemini tier (the stronger one) with Ollama as the fallback. Each has ask(system, user) -> text."""
import collections, json, os, re, threading, time, urllib.request, urllib.error

from ..orchestrator import LLMUnavailable

ENGINE_ROOM = os.environ.get("ENGINEROOM_URL", "http://127.0.0.1:8898")
GEMMA_MODELS = ("gemma-4-26b-a4b-it", "gemma-4-31b-it")        # the 26B mixture-of-experts answers reliably; the 31B is tried second (it returned HTTP 500 for hours when this was written)
GEMINI_NATIVE = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"


def _engine(path, body=None, timeout=3):
    req = urllib.request.Request(ENGINE_ROOM + path, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


class Reply(str):
    """a model's text that remembers which model actually produced it"""
    model = ""


def _reply(text, model):
    r = Reply(text)
    r.model = model
    return r


class GemmaHinter:
    """Gemma 4 through the native Gemini API. Free-tier limits are respected by pacing (default 20 requests and 11,000 tokens per minute against the 30 / 16,000 caps), the engine room's budget
    is checked and every call is reported to it. The Gemma API has no system role: the instructions are folded into the user message. Replies often start with the model's reasoning;
    callers extract the JSON (jsonx.extract_json)."""
    name = "gemma"

    def __init__(self, models=GEMMA_MODELS, rpm=20, tpm=13000, retries=5, sleep=time.sleep, now=time.time):
        self.models, self.rpm, self.tpm, self.retries, self.sleep, self.now = list(models), rpm, tpm, retries, sleep, now
        self.window = collections.deque()                             # (time, tokens)
        self.lock = threading.Lock()
        self.used = collections.Counter()
        self.failed = collections.Counter()
        self.label = "+".join(models)
        self.workers = 3                                              # a call takes ~2 minutes (the model thinks first): three in flight fit the 30 requests / 16,000 tokens per minute

    def _key(self):
        k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not k:
            raise LLMUnavailable("no Gemini key in the environment")
        return k

    def _pace(self, est):
        """block until one more request of about `est` tokens fits under both per-minute caps, then RESERVE it (so parallel workers cannot all pass at once); returns the reservation,
        which the caller shrinks to the real size on success or removes (release) when the attempt fails"""
        while True:
            with self.lock:
                t = self.now()
                while self.window and t - self.window[0][0] >= 60:
                    self.window.popleft()
                if len(self.window) < self.rpm and sum(x[1] for x in self.window) + est <= self.tpm:
                    entry = [t, est]
                    self.window.append(entry)
                    return entry
                wait = 60 - (t - self.window[0][0]) + 0.5 if self.window else 1.0
            self.sleep(min(max(wait, 0.5), 30))

    def _release(self, slot):
        with self.lock:
            try:
                self.window.remove(slot)
            except ValueError:
                pass

    def ask(self, system, user):
        key = self._key()
        text = (system + "\n\n" + user) if system else user
        est = len(text) // 4 + 5800                                   # the prompt plus the model's own thinking and the reply (measured ~6,300 tokens for a 12-tool chunk)
        last = None
        for model in self.models:
            try:
                if not _engine("/budget?model=%s" % model).get("ok_to_send", True):
                    last = "the engine-room budget for %s is used up for now" % model
                    continue
            except Exception:
                pass                                                  # engine room not running: the provider's own limits still apply
            for attempt in range(self.retries):
                slot = self._pace(est)
                body = dict(contents=[dict(role="user", parts=[dict(text=text)])], generationConfig=dict(temperature=0.1, maxOutputTokens=8192, responseMimeType="application/json"))
                req = urllib.request.Request(GEMINI_NATIVE % model, json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
                try:
                    with urllib.request.urlopen(req, timeout=180) as r:
                        resp = json.loads(r.read())
                except urllib.error.HTTPError as e:
                    self._release(slot)                                   # a rejected request used no quota: give its reservation back
                    last = "%s: HTTP %d" % (model, e.code)
                    self.failed[model] += 1
                    if e.code in (429, 500, 502, 503, 504) and attempt < self.retries - 1:
                        self.sleep((2, 6, 15, 30, 60)[min(attempt, 4)])                   # Gemma 4 answers HTTP 500 in bursts: wait it out
                        continue
                    break                                             # try the next model
                except Exception as e:
                    self._release(slot)
                    last = "%s: %s" % (model, str(e)[:80])
                    break
                usage = resp.get("usageMetadata") or {}
                if usage.get("totalTokenCount"):
                    slot[1] = usage["totalTokenCount"]                  # the real size replaces the estimate
                parts = ((resp.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                out = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                try:
                    _engine("/llm", dict(backend="gemini", who="agentdispatch-index", model=model, tokens=usage.get("totalTokenCount", 0)), timeout=2)
                except Exception:
                    pass
                self.used[model] += 1
                if out.strip():
                    return _reply(out, model)
                last = "%s: empty reply" % model
        raise LLMUnavailable(last or "no Gemma model answered")


_LOCAL_GATE = threading.Semaphore(1)                                   # one local generation at a time, whoever asks (parallel index workers fall through here together)


class OllamaHinter:
    name = "ollama"

    def __init__(self, models, url="http://127.0.0.1:11434"):
        self.models, self.url = list(models), url
        self.label = "+".join(models)
        self.used = collections.Counter()
        self.failed = collections.Counter()

    def ask(self, system, user):
        from .build import ask_ollama
        last = None
        for m in self.models:
            try:
                with _LOCAL_GATE:
                    text, _ = ask_ollama(m, system, user)
                self.used[m] += 1
                return _reply(text, m)
            except Exception as e:
                self.failed[m] += 1
                last = "%s: %s" % (m, str(e)[:80])
        raise LLMUnavailable(last or "no local model")
