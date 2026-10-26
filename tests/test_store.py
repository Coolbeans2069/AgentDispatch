import os, sys, tempfile, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.store import Board, BoardError, CAPS, RATE_PER_MIN, LEASE_MIN
from agentdispatch.redact import redact


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


FAKE_GOOGLE_KEY = "AIza" + "SyA1234567890" + "abcdefghijklmnopqrstuvwx"   # a fake key for the redaction tests, assembled so secret scanners do not flag the source

def mk():
    d = tempfile.mkdtemp(prefix="board_")
    c = Clock()
    return Board(d, now=c), c, d


def raises(code, f, *a, **k):
    try:
        f(*a, **k)
    except BoardError as e:
        assert e.code == code, (e.code, code)
        return e
    raise AssertionError("expected BoardError %s" % code)


def test_redaction():
    t, f = redact("key " + FAKE_GOOGLE_KEY + " and password = hunter2222 and Bearer abcdefghijklmnop1234 ok")
    assert "AIza" not in t and "hunter2222" not in t and "abcdefghijklmnop1234" not in t
    assert {"google-key", "assignment", "bearer"} <= set(f)
    assert redact("nothing secret here")[1] == []


def test_post_caps_and_redaction():
    b, c, d = mk()
    r = b.post("claude", "issue", "Mixer exits 1 on a failed gate", "x" * 10000 + " token=abcdef123456", tags=["Mixer", "exit code!", "a" * 100], severity=2)   # gitleaks:allow (fake value: the test checks it is redacted)
    assert r["ok"] and r["id"] == "P-000001" and "note" in r
    p = b.trail("P-000001")["issue"]
    assert len(p["body"]) <= CAPS["body"] and "abcdef123456" not in p["body"]
    assert p["tags"][0] == "mixer" and len(p["tags"][2]) <= 32
    raises("bad_kind", b.post, "x", "rant", "t")
    raises("empty", b.post, "x", "issue", "   ")
    assert b.rebuild_check() == []
    shutil.rmtree(d, ignore_errors=True)


def test_rate_limit():
    b, c, d = mk()
    for i in range(RATE_PER_MIN):
        b.post("spam", "status", "s%d" % i)
    raises("rate_limited", b.post, "spam", "status", "one too many")
    c.t += 61
    assert b.post("spam", "status", "ok again")["ok"]
    shutil.rmtree(d, ignore_errors=True)


def test_threads_branch_and_trail():
    b, c, d = mk()
    root = b.post("grok", "issue", "ComfyUI window hangs", "8189 sits at 0% GPU")["id"]
    a1 = b.reply("codex", root, "maybe the model load froze the web server", kind="answer")["id"]
    b.reply("claude", a1, "confirmed: /history polls time out", kind="answer")
    b.reply("gemini", root, "which model was loading?", kind="question")
    t = b.trail(root)
    assert [n["id"] for n in t["threads"]] == [a1, "P-000004"]
    assert t["threads"][0]["children"][0]["author"] == "claude"
    assert t["open_questions"] == ["P-000004"]
    md = open(os.path.join(d, "board", "trails", "%s.md" % root), encoding="utf-8").read()
    assert "ComfyUI window hangs" in md and "confirmed" in md
    shutil.rmtree(d, ignore_errors=True)


def test_claim_lease():
    b, c, d = mk()
    i = b.post("a", "issue", "needs a fix")["id"]
    assert b.claim("one", i)["ok"]
    e = raises("claimed", b.claim, "two", i)
    assert e.extra["claimed_by"] == "one"
    assert b.read(state="claimed")["count"] == 1
    c.t += 60 * (LEASE_MIN + 1)                                  # the lease runs out: free again, visible as open
    assert b.read(state="open")["count"] == 1 and b.read(state="claimed")["count"] == 0
    assert b.claim("two", i)["ok"]
    assert b.expire_leases() == 0
    c.t += 60 * 300
    assert b.expire_leases() == 1
    raises("not_claimable", b.claim, "x", b.post("a", "status", "just info")["id"])
    shutil.rmtree(d, ignore_errors=True)


def test_attachments_paged_and_linked():
    b, c, d = mk()
    i = b.post("a", "issue", "long log")["id"]
    big = "line one of the log\n" + ("0123456789" * 1000) + "\napi_key=SECRETSECRET12"
    r = b.attach("a", i, "the full log", big)
    assert r["ok"] and r["pages"] == 3 and "assignment" in r["redacted"]
    p1 = b.read_doc(r["doc"], 1)
    assert p1["page"] == 1 and p1["pages"] == 3 and "SECRETSECRET12" not in b.read_doc(r["doc"], 3)["text"]
    assert b.read_doc(r["doc"], 99)["page"] == 3                 # pages are clamped
    t = b.trail(i)
    assert t["docs"][0]["summary"].startswith("line one")
    raises("too_big", b.attach, "a", i, "huge", "x" * (CAPS["doc_bytes"] + 1))
    raises("not_found", b.read_doc, "D-999999")
    shutil.rmtree(d, ignore_errors=True)


def test_attempt_ledger_stops_repeats():
    b, c, d = mk()
    i = b.post("a", "issue", "farm times out on 8189")["id"]
    r1 = b.attempt("claude", i, "Raise the history poll timeout from 30 s to 60 s in media_farm.py", "no more timeouts")
    assert r1["ok"]
    b.result("claude", r1["attempt"], "failed", "still timed out after 31 s: the server was frozen, not slow")
    # the same idea, reworded and with different numbers, is recognised as already tried
    r2 = b.attempt("codex", i, "raise the poll timeout of the history request to 120 s inside media_farm.py")
    assert not r2["ok"] and r2["error"] == "already_tried" and r2["prior"][0]["attempt"] == r1["attempt"]
    assert "frozen" in r2["prior"][0]["evidence"]
    # a genuinely different approach is allowed
    r3 = b.attempt("codex", i, "keep polling through OSError until 20 minutes of silence before failing the shot")
    assert r3["ok"]
    # challenging the earlier attempt lets the same idea be retried, and records the challenge in the thread
    r4 = b.attempt("gemini", i, "raise the poll timeout of the history request to 60 s in media_farm.py", challenge="the earlier test ran against the wrong port")
    assert r4["ok"]
    t = b.trail(i)
    assert any(x["kind"] == "challenge" for x in t["threads"]) and t["dead_ends"][0]["attempt"] == r1["attempt"]
    assert [a["attempt"] for a in t["in_progress"]] == [r3["attempt"], r4["attempt"]]
    # someone else already trying nearly the same thing right now
    r5 = b.attempt("grok", i, "keep polling through OSError until 20 min of silence before failing the shot")
    assert not r5["ok"] and r5["error"] == "in_progress"
    raises("bad_outcome", b.result, "x", r3["attempt"], "maybe")
    b.result("codex", r3["attempt"], "worked", "shot finished after the freeze")
    raises("already_recorded", b.result, "codex", r3["attempt"], "worked")
    r6 = b.attempt("grok", i, "keep polling through OSError until twenty minutes of silence before failing the shot")
    assert not r6["ok"] and r6["error"] == "already_solved_by"
    shutil.rmtree(d, ignore_errors=True)


def test_escalation_after_three_failures():
    b, c, d = mk()
    i = b.post("a", "issue", "unfixable")["id"]
    for n, appr in enumerate(["reinstall the driver", "downgrade the model checkpoint", "swap the cable", ]):
        a = b.attempt("bot", i, appr)
        r = b.result("bot", a["attempt"], "failed", "no change %d" % n)
    assert r["escalated"] is True
    assert b.read(state="escalated")["count"] == 1
    assert b.trail(i)["state"] == "escalated"
    shutil.rmtree(d, ignore_errors=True)


def test_resolve_archives_and_search_finds_it():
    b, c, d = mk()
    i = b.post("a", "issue", "gate exits with code one instead of two", "ad_gyro uses sys.exit(str)", tags=["mixer"])["id"]
    a = b.attempt("claude", i, "print the message and call sys.exit(2)")
    out = b.resolve("claude", i, "exit 2 on a failed gate", "test_gyro passes", attempt=a["attempt"])
    assert os.path.isfile(out["archived"]) and out["archived"].endswith("%s.md" % i)
    assert "exit 2 on a failed gate" in open(out["archived"], encoding="utf-8").read()
    assert b.read()["count"] == 0 and b.read(state="solved")["count"] == 1      # solved items leave the open list
    hits = b.search("sys.exit gate code")
    assert hits["count"] >= 1 and any(h["id"] == i for h in hits["results"])
    assert b.search("zzzznotpresent")["count"] == 0
    assert b.search("")["count"] == 0
    raises("closed", b.attempt, "x", i, "anything at all")
    assert b.stats()["issues"] == {"solved": 1}
    shutil.rmtree(d, ignore_errors=True)


def test_suggest_and_events():
    b, c, d = mk()
    i = b.post("a", "issue", "needs a strong model")["id"]
    b.suggest("owner", i, "opus", "this one is tricky")
    t = b.trail(i)
    assert t["suggestions"][0]["model"] == "opus" and t["suggestions"][0]["by"] == "owner"
    ev = b.events_since(0)
    assert [e["type"] for e in ev] == ["post", "suggest"]
    assert b.events_since(ev[0]["id"])[0]["type"] == "suggest"
    assert b.rebuild_check() == []
    shutil.rmtree(d, ignore_errors=True)


def test_review_regressions():
    # redaction: quoted JSON keys and Basic credentials used to leak
    t, f = redact('{"api_key": "abcd1234efgh5678", "ok": 1} Authorization: Basic dXNlcjpwYXNzd29yZDEyMw== password: hunter2hunter2')   # gitleaks:allow (fake values: the test checks they are redacted)
    assert "abcd1234efgh5678" not in t and "dXNlcjpwYXNzd29yZDEyMw" not in t and "hunter2hunter2" not in t and '"ok": 1' in t
    assert redact("the token count is 5 and my password is fine")[1] == []
    # an escalated issue can be claimed by only one agent at a time
    b, c, d = mk()
    i = b.post("a", "issue", "hard")["id"]
    for appr in ("alpha beta gamma delta", "epsilon zeta eta theta", "iota kappa lambda mu"):
        a = b.attempt("bot", i, appr)
        b.result("bot", a["attempt"], "failed", "no")
    assert b.trail(i)["state"] == "escalated"
    assert b.claim("A", i)["ok"]
    assert raises("claimed", b.claim, "B", i).extra["claimed_by"] == "A"
    assert b.read(state="escalated")["posts"][0]["claimed_by"] == "A"
    c.t += 60 * (LEASE_MIN + 1)
    assert b.claim("B", i)["ok"]                                                  # the lease ran out
    # an attempt whose agent vanished stops blocking after its lease
    j = b.post("a", "issue", "another")["id"]
    first = b.attempt("crashy", j, "rebuild the cache directory from the backup copy")
    assert not b.attempt("other", j, "rebuild the cache directory from the backup copy now")["ok"]       # in progress: blocked
    c.t += 61 * 60
    again = b.attempt("other", j, "rebuild the cache directory from the backup copy now")
    assert again["ok"] and [p["outcome"] for p in again["similar_earlier"]] == ["abandoned"]
    assert [a["attempt"] for a in b.trail(j)["abandoned"]] == [first["attempt"]]
    # a very short failed approach must not block a longer, more specific one
    k = b.post("a", "issue", "tests fail")["id"]
    s1 = b.attempt("x", k, "run the tests")
    b.result("x", s1["attempt"], "failed", "still red")
    assert b.attempt("y", k, "run the tests with the verbose flag and a fresh cache directory")["ok"]
    # indexed event lookups for the orchestrator
    assert b.last_event(k, "attempt")["actor"] == "y" and b.last_event(k, "triage") is None
    b.note_event(k, "triage", "orchestrator", dict(tier="mid"))
    assert b.last_event(k, "triage")["data"]["tier"] == "mid"
    raises("not_found", b.note_event, "P-999999", "triage", "x")
    b.close(); shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("ok", n)
    print("ALL STORE TESTS PASSED")
