"""wiring: one Board + one McpServer (board, agency, model, campaign and capability tools) + the audit log, shared by the stdio and HTTP front ends"""
import json, os, posixpath, threading, time

from . import __version__
from .boardtools import build as board_tools, INSTRUCTIONS
from .mcp_server import McpServer
from .store import Board

DEFAULT_DATA = os.environ.get("AGENTDISPATCH_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))


class Audit:
    """one JSON line per tool call (who, what, how long, error?) - the usage history Tool Master will be shaped from"""
    def __init__(self, data):
        self.path = os.path.join(data, "audit.jsonl")
        self.lock = threading.Lock()

    def __call__(self, rec):
        with self.lock, open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class App:
    def resolve_model(self, function):
        """the model on disk to use for a function (the person's pin if it fits, else a sensible default): a folder path, or None. Today: speech-stt -> a faster-whisper model."""
        cat = self.catalog
        if function != "speech-stt":
            return None
        rows = [m for m in cat.list(function="speech-stt", limit=50) if "faster-whisper" in (m.get("name") or "").lower() and os.path.isdir(m["path"])]
        sel = cat.d["selection"].get("speech-stt")
        if sel:
            hit = next((m for m in rows if m["id"] == sel["model_id"]), None)
            if hit:
                return hit["path"]
        for want in ("medium", "small", "large-v3", "base"):                                # medium: good accuracy on the CPU; the others if it is not there
            hit = next((m for m in rows if m["name"].lower().endswith("-" + want)), None)
            if hit:
                return hit["path"]
        return rows[0]["path"] if rows else None

    def dispatcher(self):
        """the agency's dispatcher for agents that ask for work (pull): built once, shares the board, the jobs and the sandboxed check runner"""
        if self._disp is None:
            from .agency import policy
            from .agency.dispatcher import Dispatcher
            from .agency.realadapters import RealAdapter
            from .agency.runner import CheckRunner
            from .agency.workspace import Workspace
            cfg = policy.load(self.data)
            runner = CheckRunner(self.data)

            def checker(job):
                return runner.run_job(Workspace(cfg["work_dir"], job["id"], job.get("created")).project, job.get("acceptance"), bool(cfg.get("allow_host_checks")))
            from .agency.apply import make_apply
            ad = RealAdapter(cfg["work_dir"], None, toolbox=self.localtools, governor=self.campaign.governor, book=self.availability.book)
            from .agency.agentic import make_digest
            ad.digest = make_digest(self)
            self._disp = Dispatcher(self.board, self.jobs, self.registry, self.availability, ad, self.data, cfg=cfg,
                                    checker=checker, hooks=self.campaign.governor, apply_fn=make_apply(self.data, cfg["work_dir"], runner.run))
        return self._disp

    def __init__(self, data=None):
        self.data = os.path.abspath(data or DEFAULT_DATA)
        os.makedirs(self.data, exist_ok=True)
        self.board = Board(self.data)
        from .agency.jobs import Jobs
        from .agency.registry import Registry
        from .agency.availability import Availability
        from .agency.tools import build as agency_tools
        from .nightshift import load_config

        def target_ok(t):                                                       # a code job may only name a project folder the night shift is allowed to copy from
            roots = [r.replace("\\", "/").rstrip("/").lower() for r in load_config(self.data).get("allowed_roots", [])]
            n = posixpath.normpath((t or "").replace("\\", "/")).rstrip("/").lower()            # normalised: no ".." can climb out of a root
            return bool(t) and ".." not in n.split("/") and any(n == r or n.startswith(r + "/") for r in roots)
        self.jobs, self.registry = Jobs(self.board, target_ok), Registry(self.data)
        self.availability = Availability(self.jobs, data_dir=self.data)
        self._disp = None
        from .models.catalog import Catalog
        from .models.tools import build as model_tools
        self.catalog = Catalog(self.data)
        try:
            from . import openrouter as _or
            _or.ensure_in_catalog(self.catalog)                                     # free OpenRouter models always visible in /models and /brain
        except Exception:
            pass
        from . import localtools, campaign
        from .agency import policy as agency_policy
        from .campaign.api import Api
        from .campaign.mcp import build as campaign_tools
        self.localtools = localtools.build(self.data, load_config(self.data).get("allowed_roots", []), agency_policy.load(self.data)["work_dir"])
        self.localtools.resolve_model = self.resolve_model
        from .usage import Usage
        self.usage = Usage(self)

        def local_chat(prompt):
            from .intake.chat import chat_model
            r = self.router.chat(dict(model=chat_model(self), messages=[dict(role="user", content=prompt)], max_tokens=4000, temperature=0.2), "local_llm")
            return r["choices"][0]["message"]["content"]
        self.localtools.local_chat = local_chat
        self.campaign = campaign.build(self)
        self.api = Api(self)
        self.mcp = McpServer("agentdispatch", __version__, INSTRUCTIONS, board_tools(self.board) + agency_tools(self.jobs, self.registry, self.availability, self.dispatcher) + model_tools(self.catalog)
                             + campaign_tools(self.api))
        self.mcp.log = Audit(self.data)
        from .agency.capabilities import Capabilities
        self.capabilities = Capabilities(self.data, tool_names=lambda: list(self.mcp.tools), read_only=lambda n: bool(self.mcp.tools[n].annotations.get("readOnlyHint")) if n in self.mcp.tools else False)
        for t in self.capabilities.tools():
            self.mcp.add(t)
        from .router import Router
        self.router = Router(self)
