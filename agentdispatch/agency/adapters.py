"""how an agent is actually run. Stage 0 provides the interface, the limit detector and a scripted fake; the real CLI / router adapters arrive in Stage 1 after each CLI's headless mode is tested."""
import re, time
from dataclasses import dataclass, field

# Covers CLI + API providers used on this host:
# Claude Code session, Codex plan, Gemini quota/retry-in, OpenRouter 429,
# Grok/xAI, OpenAI TPM/RPM, Anthropic overloaded, Google resource exhausted.
LIMIT_PAT = re.compile(
    r"usage limit|session limit|weekly limit|daily limit|plan limit|"
    r"rate[ _-]?limit|rate_limit_exceeded|quota[ _-]?(?:exceeded|exhausted)|"
    r"insufficient[ _-]?quota|too many requests|\b429\b|"
    r"resource[_ ]exhausted|overloaded_error|model[_ ]overloaded|"
    r"tokens? per (?:min|minute|day)|TPM|RPM|"
    r"limit (?:reached|hit)|hit your (?:session |usage |plan |weekly |daily )?limit|"
    r"out of (?:credits|usage|quota)|try again (?:later|in)|retry in|budget is used up|used up for now|no backend could answer|"
    r"billing|capacity(?: exceeded)?|"
    r"resets?\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:[ap]m)?",
    re.I,
)


def detect_limit(text):
    """does this output say the agent hit a plan / rate / session limit (as opposed to failing at the task)?"""
    return bool(LIMIT_PAT.search(text or ""))


_AT = re.compile(
    r"(?:try again|available|resets?)\s+(?:at|after)\s+(\d{1,2}):(\d{2})\s*([AP]M)",
    re.I,
)
_IN = re.compile(r"try again in (\d+)\s*(hour|minute|min|hr|h|m)", re.I)
_RETRY = re.compile(r"retry in (?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", re.I)         # Gemini: "Please retry in 12h27m26.5s"
# Claude Code: "You've hit your session limit · resets 3:20am" / "resets 4:00 PM" / "resets at 3:20am"
_RESETS = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])?", re.I)


def parse_reset(text, now=None):
    """when an agent says when its limit resets ("try again at 8:47 PM", "resets 3:20am") -> epoch seconds, else None"""
    now = now or time.time()
    m = _AT.search(text or "")
    if m:
        h, mi, ap = int(m.group(1)) % 12, int(m.group(2)), m.group(3).upper()
        t = time.localtime(now)
        ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h + (12 if ap == "PM" else 0), mi, 0, 0, 0, -1))
        return ts if ts > now else ts + 86400
    m = _RESETS.search(text or "")
    if m:
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        ap = (m.group(3) or "").upper()
        t = time.localtime(now)
        if ap in ("AM", "PM"):
            h = h % 12 + (12 if ap == "PM" else 0)
        elif h <= 12 and t.tm_hour >= 12:
            # bare "3:20" after noon usually means the next AM clock time
            pass
        ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h % 24, mi, 0, 0, 0, -1))
        return ts if ts > now + 60 else ts + 86400
    m = _RETRY.search(text or "")
    if m and any(m.groups()):
        return now + int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)
    m = _IN.search(text or "")
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return now + n * (3600 if unit in ("hour", "hr", "h") else 60)
    return None


@dataclass
class Result:
    ok: bool
    text: str = ""
    limit_hit: bool = False
    error: str = ""
    touched: list = field(default_factory=list)        # repo-relative paths the run changed
    diff_lines: int = 0
    ref: str = ""                                      # where the transcript / patch was attached


class Adapter:
    def run(self, agent, step, job, workspace=None):
        """execute one step -> Result"""
        raise NotImplementedError("real agent runs arrive in Stage 1; Stage 0 only plans and assigns")

    def review(self, agent, job, results):
        """an independent review of a job's results -> dict(approve=bool, reasons=[...])"""
        raise NotImplementedError("real reviews arrive in Stage 1")

    # request / apply halves, used for agents that ask for work (pull)
    def step_request(self, agent, step, job):
        raise NotImplementedError

    def step_apply(self, agent, step, job, text):
        raise NotImplementedError

    def plan_request(self, job):
        raise NotImplementedError

    def plan_apply(self, text):
        raise NotImplementedError

    def review_request(self, job, results, agent=None):
        raise NotImplementedError

    def review_apply(self, text):
        raise NotImplementedError


class FakeAdapter(Adapter):
    """scripted behaviour for tests: scripts[agent_id] is a list of Result (or callables returning one), used in order; the last repeats. reviews[agent_id] likewise (dicts)."""
    def __init__(self, scripts=None, reviews=None):
        self.scripts, self.reviews, self.calls, self.review_calls = dict(scripts or {}), dict(reviews or {}), [], []

    @staticmethod
    def _next(table, key):
        seq = table.get(key)
        if not seq:
            return None
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        return item() if callable(item) else item

    def run(self, agent, step, job, workspace=None):
        self.calls.append((agent["id"], job["id"], step["n"]))
        r = self._next(self.scripts, agent["id"])
        return r if r is not None else Result(ok=True, text="done (fake)")

    def step_request(self, agent, step, job):
        return "PROMPT job %s step %s: %s" % (job["id"], step["n"], step["objective"])

    def step_apply(self, agent, step, job, text):
        if text == "LIMIT":
            return Result(ok=False, limit_hit=True, error="usage limit reached")
        if text.startswith("FAIL"):
            return Result(ok=False, error="bad answer")
        return Result(ok=True, text=text, touched=[], diff_lines=0)

    def plan_request(self, job):
        return "PROMPT plan " + job["id"]

    def plan_apply(self, text):
        import json
        d = json.loads(text)
        return d["steps"]

    def review_request(self, job, results, agent=None):
        return "PROMPT review " + job["id"]

    def review_apply(self, text):
        import json
        return json.loads(text)

    def review(self, agent, job, results):
        self.review_calls.append(agent["id"])
        r = self._next(self.reviews, agent["id"])
        return r if r is not None else dict(approve=True, reasons=[])
