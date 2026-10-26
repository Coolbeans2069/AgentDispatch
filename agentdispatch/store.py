"""the notice board: an append-only, event-sourced store (SQLite) with branching threads, attachments, claims with leases, and an attempt ledger that stops agents repeating a failed step.

Everything that happens is an EVENT (never an edit); the `posts` table is a projection kept in the same transaction. Solved issues are archived as Markdown under board/archive/YYYY-MM/
(the ".bak"); long material lives in attachment documents under board/docs/<post-id>/ and posts only carry a link and a one-line summary.
"""
import json, os, re, sqlite3, threading, time, hashlib, datetime

from .redact import redact
from .similarity import fingerprint, similarity
from .textfit import fit as _fit

KINDS = ("status", "issue", "question", "answer", "solution", "tool-note", "challenge", "suggest")
OUTCOMES = ("worked", "failed", "partial")
CAPS = dict(title=120, body=8000, tags=8, tag_len=32, doc_bytes=200_000, evidence=4000, approach=2000, page=4000)      # body / evidence / approach reach agents (similar_earlier, the night shift): raised from 1500 / 1200 / 800
RATE_PER_MIN = 60
LEASE_MIN = 30
ATTEMPT_LEASE_MIN = 60      # an attempt with no result after this long is treated as abandoned (its agent crashed or stopped) and no longer blocks others
REPEAT_SIM = 0.70          # an approach this similar to a FAILED one counts as already tried
ESCALATE_FAILS = 3         # failed attempts on one issue before it is escalated


class BoardError(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.extra = code, extra

    def as_dict(self):
        return dict(ok=False, error=self.code, message=str(self), **self.extra)


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, post_id TEXT, type TEXT NOT NULL, actor TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, ts REAL, author TEXT, kind TEXT, title TEXT, body TEXT, tags TEXT, ref TEXT, severity INTEGER DEFAULT 0,
  parent_id TEXT, root_id TEXT, state TEXT, claimed_by TEXT, claim_until REAL DEFAULT 0, attempts INTEGER DEFAULT 0, fails INTEGER DEFAULT 0, summary TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, post_id TEXT, actor TEXT, approach TEXT, fp TEXT, expected TEXT, outcome TEXT, evidence TEXT, ts REAL, ts_done REAL, challenge_of TEXT);
CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, post_id TEXT, title TEXT, path TEXT, sha256 TEXT, bytes INTEGER, summary TEXT, ts REAL, actor TEXT);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, n INTEGER);
CREATE INDEX IF NOT EXISTS i_posts_root ON posts(root_id); CREATE INDEX IF NOT EXISTS i_posts_state ON posts(state);
CREATE INDEX IF NOT EXISTS i_att_post ON attempts(post_id); CREATE INDEX IF NOT EXISTS i_ev_post_type ON events(post_id, type);
"""


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "doc"


class Board:
    def __init__(self, data_dir, now=time.time):
        self.dir = os.path.abspath(data_dir)
        self.now = now
        os.makedirs(os.path.join(self.dir, "board", "docs"), exist_ok=True)
        os.makedirs(os.path.join(self.dir, "board", "archive"), exist_ok=True)
        os.makedirs(os.path.join(self.dir, "board", "trails"), exist_ok=True)
        self.db = sqlite3.connect(os.path.join(self.dir, "board.sqlite"), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)
        self.fts = True
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(kind UNINDEXED, ref_id UNINDEXED, text)")
        except sqlite3.OperationalError:
            self.fts = False
        self._rate = {}

    # ------------------------------------------------------------ helpers
    def _next(self, name, prefix):
        r = self.db.execute("SELECT n FROM counters WHERE name=?", (name,)).fetchone()
        n = (r["n"] if r else 0) + 1
        self.db.execute("INSERT OR REPLACE INTO counters(name,n) VALUES(?,?)", (name, n))
        return "%s-%06d" % (prefix, n)

    def _event(self, post_id, typ, actor, data=None):
        self.db.execute("INSERT INTO events(ts,post_id,type,actor,data) VALUES(?,?,?,?,?)", (self.now(), post_id, typ, actor, json.dumps(data or {}, ensure_ascii=False)))

    def _rate_ok(self, actor):
        t = self.now()
        q = [x for x in self._rate.get(actor, []) if t - x < 60]
        if len(q) >= RATE_PER_MIN:
            self._rate[actor] = q
            return False
        q.append(t)
        self._rate[actor] = q
        return True

    def _fts_put(self, kind, ref_id, text):
        if self.fts:
            self.db.execute("DELETE FROM fts WHERE kind=? AND ref_id=?", (kind, ref_id))
            self.db.execute("INSERT INTO fts(kind,ref_id,text) VALUES(?,?,?)", (kind, ref_id, text))

    def _post_row(self, pid):
        r = self.db.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        if not r:
            raise BoardError("not_found", "no such post: %s" % pid)
        return r

    def _eff_state(self, r):
        if r["state"] in ("claimed", "investigating") and r["claim_until"] and r["claim_until"] < self.now():
            return "open"                                              # the lease ran out: the issue is free again
        return r["state"]

    def _view(self, r, brief=True):
        d = dict(id=r["id"], kind=r["kind"], title=r["title"], state=self._eff_state(r), author=r["author"], tags=json.loads(r["tags"] or "[]"),
                 severity=r["severity"], parent=r["parent_id"], attempts=r["attempts"], fails=r["fails"], claimed_by=r["claimed_by"] if (r["claimed_by"] and r["claim_until"] and r["claim_until"] > self.now()) else None,
                 age_min=round((self.now() - r["ts"]) / 60.0, 1), summary=r["summary"] or "")
        if not brief:
            d["body"] = r["body"]
            d["ref"] = r["ref"]
        return d

    def _clean(self, text, cap):
        text, found = redact(text or "")
        return (text[:cap] if cap < 1000 else _fit(text, cap, "text")), found          # a long board text over its cap keeps head + tail with a visible marker: agents read these (it was a silent cut); a title stays a plain slice

    # ------------------------------------------------------------ writing
    def post(self, actor, kind, title, body="", tags=(), ref="", severity=0, parent=None):
        if kind not in KINDS:
            raise BoardError("bad_kind", "kind must be one of %s" % ", ".join(KINDS))
        with self.lock:
            if not self._rate_ok(actor):
                raise BoardError("rate_limited", "too many posts from %s in the last minute" % actor)
            title, f1 = self._clean(title, CAPS["title"])
            if not title.strip():
                raise BoardError("empty", "a post needs a title")
            long_body = len(body or "") > CAPS["body"]
            body, f2 = self._clean(body, CAPS["body"])
            tags = [re.sub(r"[^a-z0-9._\-]", "", str(t).lower())[:CAPS["tag_len"]] for t in list(tags)[:CAPS["tags"]]]
            tags = [t for t in tags if t]
            root = None
            if parent:
                pr = self._post_row(parent)
                root = pr["root_id"] or pr["id"]
            pid = self._next("post", "P")
            ts = self.now()
            self.db.execute("BEGIN")
            try:
                self.db.execute("INSERT INTO posts(id,ts,author,kind,title,body,tags,ref,severity,parent_id,root_id,state,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (pid, ts, actor, kind, title, body, json.dumps(tags), (ref or "")[:200], int(severity or 0), parent, root or pid, "open" if kind in ("issue", "question") else "info", ts))
                self._event(pid, "post", actor, dict(kind=kind, parent=parent))
                self._fts_put("post", pid, " ".join([title, body, " ".join(tags)]))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            self._write_trail(root or pid)
            out = dict(ok=True, id=pid, redacted=sorted(set(f1 + f2)))
            if long_body:
                out["note"] = "body was over %d characters: its middle was cut (marked in the text); put long material in board_attach and link it" % CAPS["body"]
            return out

    def reply(self, actor, post_id, body, kind="answer", title=None, tags=()):
        pr = self._post_row(post_id)
        t = title or ("re: " + (pr["title"] or ""))[:CAPS["title"]]
        return self.post(actor, kind, t, body, tags, parent=post_id)

    def claim(self, actor, post_id, minutes=LEASE_MIN):
        with self.lock:
            r = self._post_row(post_id)
            st = self._eff_state(r)
            if r["kind"] not in ("issue", "question"):
                raise BoardError("not_claimable", "only issues and questions can be claimed")
            if st in ("solved", "archived"):
                raise BoardError("closed", "this issue is already %s" % st)
            if r["claimed_by"] and r["claimed_by"] != actor and r["claim_until"] and r["claim_until"] > self.now():          # the lease decides, whatever the state (escalated issues too)
                raise BoardError("claimed", "claimed by %s" % r["claimed_by"], claimed_by=r["claimed_by"], minutes_left=round((r["claim_until"] - self.now()) / 60.0, 1))
            until = self.now() + 60 * max(1, min(240, int(minutes)))
            self.db.execute("UPDATE posts SET state=CASE WHEN state='escalated' THEN 'escalated' ELSE 'claimed' END, claimed_by=?, claim_until=?, updated=? WHERE id=?", (actor, until, self.now(), post_id))
            self._event(post_id, "claim", actor, dict(until=until))
            self._write_trail(r["root_id"] or post_id)
            return dict(ok=True, id=post_id, lease_minutes=int(minutes))

    def release(self, actor, post_id):
        with self.lock:
            r = self._post_row(post_id)
            if r["claimed_by"] not in (None, actor):
                raise BoardError("not_yours", "claimed by %s" % r["claimed_by"])
            self.db.execute("UPDATE posts SET state=CASE WHEN state IN ('claimed','investigating') THEN 'open' ELSE state END, claimed_by=NULL, claim_until=0, updated=? WHERE id=?", (self.now(), post_id))
            self._event(post_id, "release", actor)
            return dict(ok=True)

    def attach(self, actor, post_id, title, content):
        with self.lock:
            r = self._post_row(post_id)
            if not self._rate_ok(actor):
                raise BoardError("rate_limited", "too many writes from %s" % actor)
            text, found = redact(content or "")
            raw = text.encode("utf-8")
            if len(raw) > CAPS["doc_bytes"]:
                raise BoardError("too_big", "attachment over %d bytes: split it" % CAPS["doc_bytes"])
            did = self._next("doc", "D")
            folder = os.path.join(self.dir, "board", "docs", r["root_id"] or post_id)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "%s-%s.md" % (did, _slug(title)))
            with open(path, "wb") as f:
                f.write(raw)
            first = next((l.strip() for l in text.splitlines() if l.strip()), "")[:160]
            self.db.execute("INSERT INTO docs(id,post_id,title,path,sha256,bytes,summary,ts,actor) VALUES(?,?,?,?,?,?,?,?,?)",
                            (did, post_id, (title or "")[:CAPS["title"]], path, hashlib.sha256(raw).hexdigest(), len(raw), first, self.now(), actor))
            self._event(post_id, "attach", actor, dict(doc=did, bytes=len(raw)))
            self._fts_put("doc", did, (title or "") + " " + text[:20000])
            self._write_trail(r["root_id"] or post_id)
            return dict(ok=True, doc=did, bytes=len(raw), pages=max(1, -(-len(text) // CAPS["page"])), redacted=found)

    def read_doc(self, doc_id, page=1):
        d = self.db.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
        if not d:
            raise BoardError("not_found", "no such document: %s" % doc_id)
        text = open(d["path"], encoding="utf-8", errors="replace").read()
        pages = max(1, -(-len(text) // CAPS["page"]))
        page = max(1, min(pages, int(page or 1)))
        return dict(ok=True, doc=doc_id, title=d["title"], post=d["post_id"], page=page, pages=pages, text=text[(page - 1) * CAPS["page"]: page * CAPS["page"]], sha256=d["sha256"])

    def suggest(self, actor, post_id, model, note=""):
        """a human (or orchestrator) suggests which model should take this issue"""
        self._post_row(post_id)
        note, _ = self._clean(note, 300)
        self._event(post_id, "suggest", actor, dict(model=str(model)[:60], note=note))
        r = self._post_row(post_id)
        self._write_trail(r["root_id"] or post_id)
        return dict(ok=True)

    # ------------------------------------------------------------ the attempt ledger
    def attempt(self, actor, post_id, approach, expected="", challenge=""):
        """call BEFORE trying something. Returns ok+attempt id when it is new; refuses (already_tried) when a very similar approach already FAILED, unless `challenge` explains what was wrong with it."""
        with self.lock:
            r = self._post_row(post_id)
            st = self._eff_state(r)
            if st in ("solved", "archived"):
                raise BoardError("closed", "this issue is already %s" % st, trail_hint="board_trail")
            approach, _ = self._clean(approach, CAPS["approach"])
            if not approach.strip():
                raise BoardError("empty", "describe the approach in one or two sentences")
            fp = fingerprint(approach)
            prior = []
            hit_failed = None
            for a in self.db.execute("SELECT * FROM attempts WHERE post_id=? ORDER BY ts", (post_id,)):
                s = 1.0 if a["fp"] == fp else similarity(approach, a["approach"])
                stale = not a["outcome"] and self.now() - a["ts"] > 60 * ATTEMPT_LEASE_MIN
                if s >= 0.5:
                    prior.append(dict(attempt=a["id"], actor=a["actor"], outcome=a["outcome"] or ("abandoned" if stale else "in-progress"), similarity=round(s, 2), approach=_fit(a["approach"] or "", 2000, "approach"),
                                      evidence=_fit(a["evidence"] or "", 2000, "evidence")))          # an agent reads these to avoid a dead end: whole, or head + tail with a marker (they were 200 / 240 characters)
                if s >= REPEAT_SIM and a["outcome"] == "failed":
                    hit_failed = a
                if s >= REPEAT_SIM and a["outcome"] == "worked":
                    return dict(ok=False, error="already_solved_by", message="a very similar approach already worked: read it instead of repeating it", prior=prior)
            busy = [p for p in prior if p["outcome"] == "in-progress" and p["actor"] != actor and p["similarity"] >= REPEAT_SIM]
            if hit_failed is not None and not (challenge or "").strip():
                return dict(ok=False, error="already_tried",
                            message="a very similar approach already failed. Choose a different step, or pass `challenge` saying what was wrong with that attempt (wrong input, environment, a bug in it)",
                            prior=prior)
            if busy and not (challenge or "").strip():
                return dict(ok=False, error="in_progress", message="%s is already trying almost the same thing" % busy[0]["actor"], prior=prior)
            aid = self._next("attempt", "A")
            ch = None
            if (challenge or "").strip() and hit_failed is not None:
                ch = hit_failed["id"]
                cb, _ = self._clean(challenge, 500)
                self.post(actor, "challenge", "challenge to %s" % ch, cb, parent=post_id)
            self.db.execute("INSERT INTO attempts(id,post_id,actor,approach,fp,expected,ts,challenge_of) VALUES(?,?,?,?,?,?,?,?)",
                            (aid, post_id, actor, approach, fp, _fit(expected or "", 2000, "expected"), self.now(), ch))
            self.db.execute("UPDATE posts SET attempts=attempts+1, state=CASE WHEN state IN ('open','claimed') THEN 'investigating' ELSE state END, claimed_by=COALESCE(claimed_by,?), "
                            "claim_until=MAX(claim_until, ?), updated=? WHERE id=?", (actor, self.now() + 60 * LEASE_MIN, self.now(), post_id))
            self._event(post_id, "attempt", actor, dict(attempt=aid, approach=approach[:200], challenge_of=ch))
            self._fts_put("attempt", aid, approach + " " + (expected or ""))
            self._write_trail(r["root_id"] or post_id)
            return dict(ok=True, attempt=aid, similar_earlier=prior, note="when done call board_result")

    def result(self, actor, attempt_id, outcome, evidence=""):
        with self.lock:
            a = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if not a:
                raise BoardError("not_found", "no such attempt: %s" % attempt_id)
            if outcome not in OUTCOMES:
                raise BoardError("bad_outcome", "outcome must be one of %s" % ", ".join(OUTCOMES))
            if a["outcome"]:
                raise BoardError("already_recorded", "this attempt already has a result: %s" % a["outcome"])
            ev, found = self._clean(evidence, CAPS["evidence"])
            self.db.execute("UPDATE attempts SET outcome=?, evidence=?, ts_done=? WHERE id=?", (outcome, ev, self.now(), attempt_id))
            r = self._post_row(a["post_id"])
            esc = False
            if outcome == "failed":
                self.db.execute("UPDATE posts SET fails=fails+1, updated=? WHERE id=?", (self.now(), a["post_id"]))
                if r["fails"] + 1 >= ESCALATE_FAILS and r["state"] not in ("solved", "archived", "escalated"):
                    self.db.execute("UPDATE posts SET state='escalated', claimed_by=NULL, claim_until=0 WHERE id=?", (a["post_id"],))
                    self._event(a["post_id"], "escalate", "board", dict(reason="%d failed attempts" % (r["fails"] + 1)))
                    esc = True
            self._event(a["post_id"], "result", actor, dict(attempt=attempt_id, outcome=outcome))
            self._fts_put("attempt", attempt_id, a["approach"] + " " + ev)
            self._write_trail(r["root_id"] or a["post_id"])
            return dict(ok=True, escalated=esc, redacted=found)

    def resolve(self, actor, post_id, solution, evidence="", attempt=None):
        """close an issue with its solution; the solved thread is archived as Markdown (the .bak) and stays searchable"""
        with self.lock:
            r = self._post_row(post_id)
            if r["kind"] not in ("issue", "question"):
                raise BoardError("not_resolvable", "only issues and questions can be resolved")
            sol, f1 = self._clean(solution, CAPS["body"])
            ev, f2 = self._clean(evidence, CAPS["evidence"])
            if not sol.strip():
                raise BoardError("empty", "give the solution")
            self.db.execute("UPDATE posts SET state='solved', summary=?, claimed_by=NULL, claim_until=0, updated=? WHERE id=?", (sol[:200], self.now(), post_id))
            if attempt:
                self.db.execute("UPDATE attempts SET outcome=COALESCE(outcome,'worked'), evidence=COALESCE(evidence,?), ts_done=COALESCE(ts_done,?) WHERE id=?", (ev, self.now(), attempt))
            self._event(post_id, "resolve", actor, dict(solution=sol, evidence=ev))
            self._fts_put("post", post_id, " ".join([r["title"], r["body"] or "", sol]))
            path = self._archive(post_id, sol, ev)
            return dict(ok=True, archived=path, redacted=sorted(set(f1 + f2)))

    # ------------------------------------------------------------ reading
    def read(self, state=None, tag=None, kind=None, limit=20, since_id=None):
        with self.lock:
            q = "SELECT * FROM posts WHERE parent_id IS NULL"
            args = []
            if kind:
                q += " AND kind=?"; args.append(kind)
            q += " ORDER BY updated DESC LIMIT 500"
            out = []
            for r in self.db.execute(q, args):
                st = self._eff_state(r)
                if state and st != state:
                    continue
                if not state and st in ("solved", "archived") :
                    continue
                if tag and tag.lower() not in json.loads(r["tags"] or "[]"):
                    continue
                if since_id and r["id"] <= since_id:
                    continue
                out.append(self._view(r))
                if len(out) >= max(1, min(100, int(limit))):
                    break
            return dict(ok=True, count=len(out), posts=out)

    def search(self, query, limit=10):
        with self.lock:
            q = " ".join('"%s"' % w for w in re.findall(r"[A-Za-z0-9_\-\.]{2,}", query or "")[:12])
            if not q:
                return dict(ok=True, count=0, results=[])
            res, seen = [], set()
            if self.fts:
                try:
                    rows = self.db.execute("SELECT kind, ref_id, snippet(fts, 2, '[', ']', ' ... ', 14) AS s FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?", (q.replace('" "', '" OR "'), int(limit) * 3)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                for r in rows:
                    key = (r["kind"], r["ref_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    res.append(self._hit(r["kind"], r["ref_id"], r["s"]))
            res = [x for x in res if x][:int(limit)]
            return dict(ok=True, count=len(res), results=res)

    def _hit(self, kind, ref_id, snippet):
        if kind == "post":
            r = self.db.execute("SELECT * FROM posts WHERE id=?", (ref_id,)).fetchone()
            return dict(type="post", id=ref_id, title=r["title"], state=self._eff_state(r), root=r["root_id"], snippet=snippet) if r else None
        if kind == "attempt":
            a = self.db.execute("SELECT * FROM attempts WHERE id=?", (ref_id,)).fetchone()
            return dict(type="attempt", id=ref_id, post=a["post_id"], outcome=a["outcome"] or "in-progress", snippet=snippet) if a else None
        d = self.db.execute("SELECT * FROM docs WHERE id=?", (ref_id,)).fetchone()
        return dict(type="doc", id=ref_id, post=d["post_id"], title=d["title"], snippet=snippet) if d else None

    def trail(self, post_id):
        """one compact view of an issue: what was tried, what failed and why, what is current best, what is open"""
        with self.lock:
            r0 = self._post_row(post_id)
            root_id = r0["root_id"] or post_id
            root = self._post_row(root_id)
            posts = [dict(x) for x in self.db.execute("SELECT * FROM posts WHERE root_id=? ORDER BY ts", (root_id,))]
            by_parent = {}
            for p in posts:
                by_parent.setdefault(p["parent_id"], []).append(p)

            def tree(pid):
                return [dict(id=c["id"], kind=c["kind"], author=c["author"], title=c["title"], body=(c["body"] or "")[:400], children=tree(c["id"])) for c in by_parent.get(pid, [])]
            atts = [dict(attempt=a["id"], actor=a["actor"], approach=a["approach"], outcome=a["outcome"] or ("abandoned" if self.now() - a["ts"] > 60 * ATTEMPT_LEASE_MIN else "in-progress"), evidence=a["evidence"] or "", challenge_of=a["challenge_of"])
                    for a in self.db.execute("SELECT * FROM attempts WHERE post_id IN (SELECT id FROM posts WHERE root_id=?) ORDER BY ts", (root_id,))]
            docs = [dict(doc=d["id"], title=d["title"], bytes=d["bytes"], summary=d["summary"], actor=d["actor"]) for d in
                    self.db.execute("SELECT * FROM docs WHERE post_id IN (SELECT id FROM posts WHERE root_id=?) ORDER BY ts", (root_id,))]
            sugg = [json.loads(e["data"]) | dict(by=e["actor"]) for e in self.db.execute("SELECT * FROM events WHERE type='suggest' AND post_id IN (SELECT id FROM posts WHERE root_id=?) ORDER BY id", (root_id,))]
            open_q = [p["id"] for p in posts if p["kind"] == "question" and p["id"] != root_id and not by_parent.get(p["id"])]
            return dict(ok=True, issue=self._view(root, brief=False), state=self._eff_state(root),
                        dead_ends=[a for a in atts if a["outcome"] == "failed"], worked=[a for a in atts if a["outcome"] == "worked"],
                        partial=[a for a in atts if a["outcome"] == "partial"], in_progress=[a for a in atts if a["outcome"] == "in-progress"], abandoned=[a for a in atts if a["outcome"] == "abandoned"],
                        threads=tree(root_id), docs=docs, suggestions=sugg, open_questions=open_q)

    def trail_md(self, post_id):
        t = self.trail(post_id)
        i = t["issue"]
        L = ["# %s  %s" % (i["id"], i["title"]), "", "state: **%s**   author: %s   tags: %s   attempts: %d (failed %d)" % (t["state"], i["author"], ", ".join(i["tags"]), i["attempts"], i["fails"]), "", i.get("body") or ""]
        if i.get("ref"):
            L += ["", "ref: " + i["ref"]]
        for title, key in (("Dead ends (do not repeat)", "dead_ends"), ("Worked", "worked"), ("Partial", "partial"), ("In progress", "in_progress"), ("Abandoned (no result was ever recorded)", "abandoned")):
            if t[key]:
                L += ["", "## " + title]
                for a in t[key]:
                    L.append("- %s by %s: %s%s%s" % (a["attempt"], a["actor"], a["approach"], (" -> " + a["evidence"]) if a["evidence"] else "", ("  [challenges %s]" % a["challenge_of"]) if a["challenge_of"] else ""))
        if t["docs"]:
            L += ["", "## Documents"] + ["- %s %s (%d bytes): %s" % (d["doc"], d["title"], d["bytes"], d["summary"]) for d in t["docs"]]
        if t["suggestions"]:
            L += ["", "## Suggested by people"] + ["- %s suggests %s %s" % (s["by"], s["model"], s.get("note", "")) for s in t["suggestions"]]
        if t["open_questions"]:
            L += ["", "## Open questions"] + ["- " + q for q in t["open_questions"]]

        def walk(nodes, depth):
            for n in nodes:
                L.append("%s- [%s] %s %s: %s" % ("  " * depth, n["kind"], n["id"], n["author"], (n["body"] or n["title"]).replace("\n", " ")[:200]))
                walk(n["children"], depth + 1)
        if t["threads"]:
            L += ["", "## Thread"]
            walk(t["threads"], 0)
        return "\n".join(L) + "\n"

    def _write_trail(self, root_id):
        try:
            with open(os.path.join(self.dir, "board", "trails", "%s.md" % root_id), "w", encoding="utf-8", newline="\n") as f:
                f.write(self.trail_md(root_id))
        except Exception:
            pass

    def _archive(self, post_id, solution, evidence):
        r = self._post_row(post_id)
        ym = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m")
        d = os.path.join(self.dir, "board", "archive", ym)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "%s.md" % (r["root_id"] or post_id))
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.trail_md(r["root_id"] or post_id) + "\n## Solution\n%s\n\nEvidence: %s\n" % (solution, evidence or "(none given)"))
        return p

    # ------------------------------------------------------------ housekeeping and views for the bulletin bar
    def expire_leases(self):
        with self.lock:
            rows = self.db.execute("SELECT id FROM posts WHERE state IN ('claimed','investigating') AND claim_until>0 AND claim_until<?", (self.now(),)).fetchall()
            for r in rows:
                self.db.execute("UPDATE posts SET state='open', claimed_by=NULL, claim_until=0 WHERE id=?", (r["id"],))
                self._event(r["id"], "lease_expired", "board")
            return len(rows)

    def stats(self):
        with self.lock:
            c = {}
            for r in self.db.execute("SELECT * FROM posts WHERE parent_id IS NULL AND kind IN ('issue','question')"):
                st = self._eff_state(r)
                c[st] = c.get(st, 0) + 1
            return dict(ok=True, issues=c, events=self.db.execute("SELECT COUNT(*) n FROM events").fetchone()["n"], attempts=self.db.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"],
                        docs=self.db.execute("SELECT COUNT(*) n FROM docs").fetchone()["n"])

    def escalate(self, post_id, actor, reason):
        """hand an issue up the ladder by hand (the board also does this itself after 3 failed attempts)"""
        with self.lock:
            r = self._post_row(post_id)
            if r["state"] in ("solved", "archived"):
                raise BoardError("closed", "this issue is already %s" % r["state"])
            self.db.execute("UPDATE posts SET state='escalated', claimed_by=NULL, claim_until=0, updated=? WHERE id=?", (self.now(), post_id))
            self._event(post_id, "escalate", actor, dict(reason=str(reason)[:200]))
            self._write_trail(r["root_id"] or post_id)
            return dict(ok=True)

    def add_tag(self, post_id, tag, actor):
        with self.lock:
            r = self._post_row(post_id)
            tags = json.loads(r["tags"] or "[]")
            tag = re.sub(r"[^a-z0-9._\-]", "", str(tag).lower())[:CAPS["tag_len"]]
            if tag and tag not in tags and len(tags) < CAPS["tags"] + 4:
                tags.append(tag)
                self.db.execute("UPDATE posts SET tags=?, updated=? WHERE id=?", (json.dumps(tags), self.now(), post_id))
                self._event(post_id, "tag", actor, dict(tag=tag))
                self._fts_put("post", post_id, " ".join([r["title"], r["body"] or "", " ".join(tags)]))
            return dict(ok=True, tags=tags)

    def count_events(self, post_id, typ):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) n FROM events WHERE post_id=? AND type=?", (post_id, typ)).fetchone()["n"]

    def last_event(self, post_id, typ):
        """the newest event of a type for one post (an indexed lookup, not a scan of the whole log), or None"""
        with self.lock:
            e = self.db.execute("SELECT * FROM events WHERE post_id=? AND type=? ORDER BY id DESC LIMIT 1", (post_id, typ)).fetchone()
            return dict(id=e["id"], ts=e["ts"], actor=e["actor"], data=json.loads(e["data"] or "{}")) if e else None

    def note_event(self, post_id, typ, actor, data=None):
        with self.lock:
            self._post_row(post_id)
            self._event(post_id, typ, actor, data)

    def events_since(self, last_id=0, limit=100):
        with self.lock:
            return [dict(id=e["id"], ts=e["ts"], post=e["post_id"], type=e["type"], actor=e["actor"], data=json.loads(e["data"] or "{}"))
                    for e in self.db.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (int(last_id), int(limit)))]

    def rebuild_check(self):
        """the events table is the truth: every post has a 'post' event and every attempt an 'attempt' event. Returns a list of problems (empty = consistent)."""
        with self.lock:
            probs = []
            for r in self.db.execute("SELECT id FROM posts"):
                if not self.db.execute("SELECT 1 FROM events WHERE post_id=? AND type='post'", (r["id"],)).fetchone():
                    probs.append("post %s has no post event" % r["id"])
            for a in self.db.execute("SELECT id, post_id FROM attempts"):
                if not self.db.execute("SELECT 1 FROM events WHERE post_id=? AND type='attempt' AND data LIKE ?", (a["post_id"], '%"%s"%' % a["id"])).fetchone():
                    probs.append("attempt %s has no attempt event" % a["id"])
            return probs

    def close(self):
        self.db.close()
