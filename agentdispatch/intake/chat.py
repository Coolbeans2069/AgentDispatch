"""the intake chat: a model on this computer reads what the person said, with the form so far and what the attachments turned out to be, and answers with a short reply plus the form filled in more
fully. Everything it returns is validated before it touches the form (unknown fields dropped, the check command held to the same allow-list the agency uses, steps and kinds checked), and when no
model answers the person's own words are simply kept in the goal. Command Centre mode loads the playbook MD so the guide can start any domain job."""
import json, os, re, shlex
from .. import paths

from ..jsonx import extract_json
from ..nightshift import command_ok
from ..agency.jobs import CAPS, KINDS, NEEDS
from ..textfit import fit

REQ_MAX = 4000                                                                  # one requirement, whole (it was cut to 200 characters)
from ..campaign import templates as T

SYSTEM = ("You are the intake assistant of an AI engineering agency. A person describes work they want done. Your job is to turn what they say into a complete, detailed job form and to ask only the "
          "questions that really matter. Reply with ONE JSON object and nothing else: "
          '{"reply": "<one or two friendly sentences; ask at most two questions>", "form": {"title": "<short, under 100 characters>", "goal": "<the complete goal: everything the person said, in detail, '
          'keeping what is already there and ADDING what is new>", "kind": "code | general | production", "target": "<a folder path ONLY if the person named one>", "check": "<ONLY a command like `python '
          'tools/acceptance.py` or `python -m pytest -q` if the person gave or clearly implied one>", "rubric": "<what a reviewer must see to accept the result>", "priority": 0-3, "requirements": ["<short '
          'bullets of concrete requirements and constraints>"], "steps": [{"objective": "<what to do>", "needs": ["code"]}], "template": "<a template id or empty>", "gate": "", "campaign": ""}}. '
          "Rules: never invent file paths or facts the person did not give; keep numbers and units exactly; leave a field out when you have nothing new for it; kind is code when files in a project folder "
          "must change, production for long builds / renders, otherwise general. needs values come from: " + ", ".join(NEEDS) + ".")

JSON_SHAPE = (
    'Reply with ONE JSON object and nothing else: '
    '{"reply": "<friendly guidance; at most two questions; always end with the one next action>", '
    '"form": {"title": "<short, under 100 characters>", "goal": "<complete goal: keep what is there and ADD what is new>", '
    '"kind": "code | general | production", "target": "<folder under the projects root (<projects_root>/<slug>) unless the person named another>", '
    '"check": "<ONLY python <script.py> or python -m pytest ... if given or clearly implied>", "rubric": "<what a reviewer must see>", '
    '"priority": 0-3, "requirements": ["<concrete bullets>"], "steps": [{"objective": "<what to do>", "needs": ["code"]}], '
    '"template": "<template id or empty>", "gate": "", "campaign": ""}}. '
    "Rules: never invent paths or facts; keep numbers and units; leave a field out when you have nothing new; "
    "kind is code when project files must change, production for long builds/renders, otherwise general. "
    "needs values: " + ", ".join(NEEDS) + "."
)

OPS_SYSTEM = (
    "You are the Ops channel of the AgentDispatch Command Centre. The person sends live course corrections while jobs run "
    "(change a requirement, nudge priority, explain a failure, pause after a job). Reply with ONE JSON object only: "
    '{"reply": "<short acknowledgement and what you will do next>", "note": "<one line for the program log>", '
    '"action": "note | repair | revise | board"}. Be concrete. Do not claim you edited merged files.'
)


def _load_playbook():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "command_centre.md")
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    return text[:14000]


def command_system():
    playbook = _load_playbook()
    head = (
        "You are the Command Centre guide for AgentDispatch. Walk the person start-to-finish from wish to agency plan. "
        "Use the playbook below. Prefer local tools and <projects_root>/<slug> for new work. "
    )
    return head + JSON_SHAPE + ("\n\n# Playbook\n" + playbook if playbook else "")


def chat_model(app):
    """the model that talks to the person: the one they pinned for 'chat / writing' on the Models page if it is an Ollama model, else whatever the router picks locally"""
    cat = getattr(app, "catalog", None)
    sel = cat.d["selection"].get("text") if cat else None
    m = cat.get(sel["model_id"]) if sel else None
    if m and (m.get("path") or "").startswith("ollama:"):
        return m["path"][7:]
    return "auto-local"


def history(session, n=10):
    return [dict(role="assistant" if m["role"] == "assistant" else "user", content=m["text"]) for m in session["messages"][-n:] if m["role"] in ("user", "assistant", "system")]


def files_note(session):
    out = []
    for f in session["files"]:
        a = f["analysis"]
        out.append("- %s (%s)%s" % (f["name"], f["kind"], (": " + a["summary"][:400]) if a.get("summary") else " (not looked at yet)" if a.get("state") in ("pending", "running") else ""))
    return "\n".join(out)


def valid_steps(steps):
    out = []
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, str) and s.strip():
            out.append(dict(objective=s.strip()[:CAPS["objective"]], needs=["research"]))
        elif isinstance(s, dict) and str(s.get("objective", "")).strip():
            needs = [n for n in (s.get("needs") or []) if n in NEEDS] or ["research"]
            out.append(dict(objective=str(s["objective"]).strip()[:CAPS["objective"]], needs=needs))
    return out[:10]


def merge(form, patch, data_dir=None):
    """-> (new form, [names of the fields that changed], [notes]). Only known fields; each one validated."""
    new, changed, notes = dict(form), [], []

    def put(k, v):
        if new.get(k) != v:
            new[k] = v
            changed.append(k)
    if not isinstance(patch, dict):
        return new, changed, notes
    for k in ("title", "rubric", "gate", "campaign", "target", "notes"):
        v = patch.get(k)
        if isinstance(v, str) and v.strip():
            put(k, v.strip()[:{"title": 120, "rubric": CAPS["rubric"], "target": 300, "notes": CAPS["why"]}.get(k, 200)])
    g = patch.get("goal")
    if isinstance(g, str) and g.strip():
        g = g.strip()[:CAPS["goal"]]
        old = form.get("goal", "")
        put("goal", g if len(g) >= 0.6 * len(old) else (old + "\n" + g)[:CAPS["goal"]])
    k = patch.get("kind")
    if k in KINDS:
        put("kind", k)
    c = patch.get("check")
    if isinstance(c, str) and c.strip():
        try:
            argv = shlex.split(c.strip())
        except ValueError:
            argv = []
        if argv and command_ok(argv):
            put("check", c.strip()[:CAPS["check"]])
        else:
            notes.append("I left the check empty: %r is not an allowed check (python <script.py> or python -m pytest ...)" % c.strip()[:80])
    p = patch.get("priority")
    if isinstance(p, int) and not isinstance(p, bool) and 0 <= p <= 3:
        put("priority", p)
    r = patch.get("requirements")
    if isinstance(r, list):
        merged = list(form.get("requirements", []))
        for x in r:
            if isinstance(x, str) and x.strip() and x.strip()[:REQ_MAX] not in merged:
                merged.append(x.strip()[:REQ_MAX])
        put("requirements", merged[:40])
    st = valid_steps(patch.get("steps"))
    if st:
        put("steps", st)
    t = patch.get("template")
    if isinstance(t, str) and t and t in T.all_templates(data_dir) and (new.get("campaign") or new.get("gate")):        # the science templates only make sense inside a campaign: never guessed for ordinary work
        put("template", t)
    return new, changed, notes


def derive(app, session, text, mode="local"):
    """-> (reply, new form, changed fields). Asks the local model through the router; falls back to keeping the person's words.
    mode 'command' loads the Command Centre playbook; anything else uses the short intake SYSTEM."""
    form = session["form"]
    bp = session.get("blueprint") or {}
    user = ("FORM SO FAR:\n%s\n\nBLUEPRINT PHASE: %s\nSUMMARY: %s\n\nATTACHMENTS:\n%s\n\nTEMPLATES: %s\n\nTHE PERSON JUST SAID:\n%s" % (
            fit(json.dumps({k: v for k, v in form.items() if v not in ("", [], None)}, ensure_ascii=False), 60000, "form"),
            session.get("phase") or "interview", fit(bp.get("summary") or "", 4000, "summary"),
            files_note(session) or "(none)", ", ".join(T.all_templates(app.data)), text))
    system = command_system() if mode == "command" else SYSTEM
    msgs = [dict(role="system", content=system)] + history(session)[:-1] + [dict(role="user", content=user)]
    reply, patch = "", None
    try:
        r = app.router.chat(dict(model=chat_model(app), messages=msgs, max_tokens=8000, temperature=0.2, response_format=dict(type="json_object")), "intake")
        raw = r["choices"][0]["message"]["content"]
        d = extract_json(raw, "form") or extract_json(raw, "reply")
        if isinstance(d, dict):
            reply, patch = str(d.get("reply", "")).strip(), d.get("form")
        if not reply and not patch:
            reply = re.sub(r"\s+", " ", raw).strip()[:400]
    except Exception as e:
        reply = "I could not reach a model on this computer just now (%s), so I kept your words in the goal. You can edit the form or try again." % str(e)[:80]
    if patch is None:                                                               # no usable answer from a model: the person's own words are not lost
        patch = dict(goal=((form.get("goal", "") + "\n" + text).strip())[:CAPS["goal"]])
        if not form.get("title"):
            patch["title"] = re.split(r"(?<=[.!?])\s", text.strip(), 1)[0][:100]
    if mode == "command" and isinstance(patch, dict):
        if not (patch.get("target") or form.get("target")):
            # Prefer an explicit path in the message (Continue prompts say "at <projects_root>/<name>")
            m = re.search(r"(?i)(?:TARGET\s+FOLDER[^:\n]*:|at)\s*((?:[A-Za-z]:)?/[^\s\n]+)", text or "")
            if m:
                patch["target"] = m.group(1).rstrip(".).,;")[:300]
            else:
                slug = re.sub(r"[^a-z0-9]+", "-", (patch.get("title") or form.get("title") or text)[:40].lower()).strip("-") or "project"
                # Never invent continue-work-on-* from a Continue title
                if slug.startswith("continue-"):
                    slug = "project"
                patch["target"] = paths.PROJECTS_ROOT.rstrip("/") + "/" + slug
        if not patch.get("kind") and not form.get("kind"):
            low = text.lower()
            if any(w in low for w in ("video", "short", "render", "ffmpeg", "trailer")):
                patch["kind"] = "production"
            elif any(w in low for w in ("website", "web app", "repo", "code", "script", "cli")):
                patch["kind"] = "code"
    new, changed, notes = merge(form, patch, app.data)
    if notes:
        reply = (reply + " " + " ".join(notes)).strip()
    if not reply or reply.lstrip().startswith("{"):
        title = new.get("title") or "this work"
        target = new.get("target") or paths.PROJECTS_ROOT.rstrip("/") + "/<slug>"
        reply = ("Got it: %s. I am aiming at %s (%s). Add constraints or press Think it through when the outline looks right." % (title[:80], target, new.get("kind") or "general"))
    return reply, new, changed


def derive_ops(app, session, text):
    """-> (reply, note, action). Ops side-chat for live course corrections once jobs exist."""
    ps = session.get("plan_status") or session.get("made") or {}
    user = ("PHASE: %s\nPLAN JOBS: %s\nFORM TITLE: %s\nOPS HISTORY (recent):\n%s\n\nTHE PERSON JUST SAID (ops):\n%s" % (
            session.get("phase") or "interview",
            fit(json.dumps(ps, ensure_ascii=False), 20000, "project status"),
            (session.get("form") or {}).get("title") or "",
            "\n".join("%s: %s" % (m.get("role"), fit(m.get("text", ""), 4000, "message")) for m in (session.get("ops_messages") or [])[-6:]),
            text))
    msgs = [dict(role="system", content=OPS_SYSTEM), dict(role="user", content=user)]
    reply, note, action = "", "", "note"
    try:
        r = app.router.chat(dict(model=chat_model(app), messages=msgs, max_tokens=2000, temperature=0.2, response_format=dict(type="json_object")), "intake-ops")
        raw = r["choices"][0]["message"]["content"]
        d = extract_json(raw, "reply") or extract_json(raw, "action") or {}
        if isinstance(d, dict):
            reply = str(d.get("reply", "")).strip()
            note = str(d.get("note", "")).strip()[:200]
            action = str(d.get("action", "note")).strip() if str(d.get("action", "note")).strip() in ("note", "repair", "revise", "board") else "note"
        if not reply:
            reply = re.sub(r"\s+", " ", raw).strip()[:400]
    except Exception as e:
        reply = "Ops noted locally (%s). I logged your correction; use Agency to inspect the job if it is already running." % str(e)[:60]
        note = text[:120]
    return reply or "Noted on the ops channel.", note or text[:120], action
