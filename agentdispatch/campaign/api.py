"""one place for what the web pages, the MCP tools and the command line can do with tools, the campaign and the playbook library, so the three front ends cannot drift apart. Every method returns a
plain dict; mistakes come back as {ok: False, error, message}, never as a crash."""
import json, os

from ..store import BoardError
from . import engineer, meta as M, templates as T

EXAMPLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")


def guard(fn):
    def h(*a, **k):
        try:
            return fn(*a, **k)
        except BoardError as e:
            return e.as_dict()
        except KeyError as e:
            return dict(ok=False, error="not_found", message="not found: %s" % e)
        except (ValueError, TypeError) as e:
            return dict(ok=False, error="bad_request", message=str(e)[:300])
    return h


class Api:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------ tools
    @property
    def box(self):
        return self.app.localtools

    @guard
    def tools(self, history=20):
        box = self.box
        listing = box.reg.listing()
        use = box.usage()
        for t in listing:
            t["runs"] = use.get(t["id"], 0)
        cfg = box.cfg
        return dict(ok=True, tools=listing, installs=box.installer.items[-20:], pending=box.installer.pending(), history=box.history(history), scanned=box.reg.state.get("scanned"),
                    pythons=[dict(path=p["path"], version=p["version"], libs=[k for k, v in p["libs"].items() if v]) for p in box.reg.state.get("pythons", [])][:12],
                    config=dict(trusted_roots=cfg.get("trusted_roots"), auto_install=cfg.get("auto_install"), python=cfg.get("python"), max_mb=cfg.get("max_mb"), allowed_roots=box.allowed_roots))

    @guard
    def scan(self):
        self.box.reg.scan(force=True)
        return self.tools()

    @guard
    def run(self, a, actor):
        r = self.box.run(a.get("tool", ""), args=a.get("args"), code=a.get("code"), file=a.get("file"), cwd=a.get("cwd"), timeout=a.get("timeout"), caller=actor, job=a.get("job_id"), project=a.get("project"),
                         strict=a.get("strict", True))
        return dict(r, ok=bool(r.get("ok")), ran=r.get("rc") is not None or bool(r.get("stdout")))

    @guard
    def install(self, a, actor):
        return self.box.installer.request(a.get("tool", ""), actor, a.get("reason", ""), a.get("python"))

    @guard
    def decide_install(self, iid, approve, actor):
        return self.box.installer.approve(iid, actor) if approve else self.box.installer.deny(iid, actor)

    @guard
    def tool_config(self, a):
        from ..localtools.toolbox import save_cfg
        cfg = self.box.cfg
        if "trusted_roots" in a:
            roots = [r for r in a["trusted_roots"] if isinstance(r, str) and os.path.isdir(r)]
            cfg["trusted_roots"] = roots
        if "auto_install" in a:
            cfg["auto_install"] = [k for k in a["auto_install"] if k in ("pip", "elan", "winget")]      # kinds the owner lets install themselves: every one still stages, is reviewed in isolation and only then used
        if a.get("python") is not None:
            cfg["python"] = a["python"] or None
        save_cfg(self.app.data, cfg)
        self.box.reload()
        return self.tools()

    @guard
    def need(self, a, actor):
        return engineer.need_tool(self.box, self.app.jobs, self.app.campaign.governor, a.get("name", ""), a.get("description", ""), a.get("examples"), actor)

    # ------------------------------------------------------------ campaign
    @guard
    def campaign(self):
        c = self.app.campaign
        snap = c.state.snapshot()
        return dict(ok=True, **snap, waiting=c.governor.waiting(), templates=[dict(id=t["id"], title=t["title"], summary=t.get("summary", ""), kind=t.get("kind"), role=t.get("role")) for t in T.all_templates(self.app.data).values()],
                    evidence_chain_ok=c.evidence.verify()[0], evidence_tip=c.evidence.tip())

    @guard
    def campaign_log(self, campaign=None, limit=60):
        return dict(ok=True, log=self.app.campaign.state.log(limit, campaign))

    @guard
    def campaign_next(self, cid):
        n = self.app.campaign.state.next_open(cid)
        return dict(ok=True, next=n, why="the earliest unclosed gate whose dependencies are all closed" if n else "nothing is open: every gate is closed or waiting")

    @guard
    def campaign_import(self, cid, text, actor):
        created, changed = self.app.campaign.state.import_blocks(cid, text, "pasted status", actor)
        return dict(ok=True, created=created, changed=changed, **self.campaign())

    @guard
    def campaign_load(self, spec, actor):
        self.app.campaign.state.load_pipeline(spec, actor)
        return self.campaign()

    @guard
    def campaign_example(self):
        p = os.path.join(EXAMPLE_FILE, "release_readiness.json")
        return dict(ok=True, example=json.load(open(p, encoding="utf-8")))

    @guard
    def propose(self, a, actor):
        return self.app.campaign.state.propose(a["campaign"], a["gate"], a["to"], a.get("job"), actor, a.get("why", ""), None, a.get("claim", ""))

    @guard
    def decide(self, pid, approve, actor, why=""):
        return self.app.campaign.state.decide(pid, approve, actor, why)

    @guard
    def transition(self, a, actor):
        g = self.app.campaign.state.transition(a["campaign"], a["gate"], a["to"], actor, a.get("job"), a.get("why", "set by the owner"))
        return dict(ok=True, gate=g)

    @guard
    def approve_run(self, job_id, actor):
        self.app.jobs.get(job_id)
        self.app.campaign.state.approve_run(job_id, actor)
        return dict(ok=True)

    # ------------------------------------------------------------ jobs from templates, evidence
    @guard
    def job_from_template(self, a, actor):
        j = self.app.campaign.governor.create_from_template(a["template"], a.get("fields") or {}, actor, a.get("with_steps", True))
        from ..agency.tools import slim
        return dict(ok=True, job=slim(j), meta=j.get("meta"))

    @guard
    def shadow(self, a, actor):
        from ..agency.tools import slim
        return dict(ok=True, job=slim(self.app.campaign.governor.spawn_shadow(a["job_id"], a.get("cutoff", ""), actor)))

    @guard
    def evidence(self, job_id):
        c = self.app.campaign
        seal = c.evidence.read_seal(job_id)
        ok, problems = c.evidence.verify_seal(job_id) if seal else (False, ["not sealed"])
        g = c.governor
        return dict(ok=True, job=job_id, sealed=bool(seal), seal=seal, seal_verifies=ok, problems=problems, result=g._read(job_id, "result.json"), reconcile=g._read(job_id, "reconcile.json"),
                    stop=g._read(job_id, "stop_report.json"), errata=c.evidence.errata(job_id), chain_tip=c.evidence.tip())

    @guard
    def erratum(self, job_id, text, actor):
        self.app.jobs.get(job_id)
        return dict(ok=True, chain=self.app.campaign.evidence.erratum(job_id, text, actor))

    # ------------------------------------------------------------ playbooks
    @guard
    def playbooks(self, q="", category="", outcome="", tool="", limit=30):
        pb = self.app.campaign.playbooks
        return dict(ok=True, total=len(pb.index), categories=pb.categories(), playbooks=pb.search(q, category, outcome, "", tool, limit))

    @guard
    def playbook(self, pid):
        pb = self.app.campaign.playbooks.get(pid)
        if not pb:
            raise KeyError(pid)
        return dict(ok=True, playbook=pb)

    @guard
    def failures(self, category="", limit=15):
        return dict(ok=True, failures=self.app.campaign.playbooks.failures(category, limit))

    @guard
    def suggest(self, goal, kind=""):
        return dict(ok=True, **self.app.campaign.playbooks.suggest(goal, kind))

    @guard
    def playbook_export(self):
        return dict(ok=True, bundle=self.app.campaign.playbooks.export())

    @guard
    def playbook_import(self, bundle):
        return dict(ok=True, imported=self.app.campaign.playbooks.import_(bundle))
