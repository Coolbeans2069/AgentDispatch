"""what a containerised agent may use, as one list that grows. Three kinds of entry:
  mcp_tools  tools of the broker's MCP server it may call through the gateway (board, playbooks, local tools, campaign state, ...) - fnmatch patterns
  shell      Bash commands it may run inside its container, as Claude Code allowedTools entries ("pytest:*")
  context    packs of files written to /board before the run (digest, job, playbooks, tools, schemas)
The list lives in <data>/agent_capabilities.json. An agent can ASK for more (capability_request, an MCP tool): a safe request (a read-only broker tool that exists, a plain command that is not on the
deny list, a known context pack) is added at once; anything else waits as a proposal the owner approves (`agency capability approve ID`, or the owner MCP tool capability_decide). Everything added is logged
with who asked and why, and can be removed again by editing the file."""
import fnmatch, json, os, re, threading, time

DEFAULT = dict(
    mcp_tools=["board_search", "board_read", "board_read_doc", "board_post", "board_reply", "board_attach", "board_attempt", "board_result", "board_suggest", "board_trail", "board_add_check", "board_claim",
               "playbook_*", "tool_list", "tool_run", "tool_need", "tool_install", "tool_search", "tool_describe", "tool_call", "tool_result",
               "job_status", "job_list", "job_evidence", "campaign_state", "model_list", "agency_agents", "capability_*"],
    shell=["python:*", "python3:*", "pytest:*", "ruff:*", "mypy:*", "git:*", "ls:*", "cat:*", "grep:*", "find:*", "sed:*", "head:*", "tail:*", "wc:*", "sort:*", "diff:*", "mkdir:*", "cp:*", "mv:*", "rm:*", "node:*"],
    context=["digest", "job", "playbooks", "tools", "schemas"],
    proposals=[], log=[])
NEVER_TOOLS = {"agency_next", "agency_submit", "job_submit", "job_cancel", "job_add_steps", "job_from_template", "board_resolve", "campaign_next", "model_select", "model_pick", "model_scan", "capability_decide"}
DENY_SHELL = {"curl", "wget", "ssh", "scp", "nc", "ncat", "telnet", "sudo", "su", "docker", "iptables", "ip6tables", "chmod", "chown", "mount", "setpriv", "nsenter", "kill", "pkill"}
PACKS = {"digest", "job", "playbooks", "tools", "schemas"}
KINDS = ("mcp_tool", "shell", "context")


class Capabilities:
    def __init__(self, data_dir, tool_names=lambda: [], read_only=lambda name: False, now=time.time):
        """tool_names() -> the broker's MCP tool names; read_only(name) -> True for a tool that does not change anything"""
        self.path = os.path.join(data_dir, "agent_capabilities.json")
        self.tool_names, self.read_only, self.now = tool_names, read_only, now
        self.lock = threading.Lock()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        out = {k: (list(d[k]) if isinstance(d.get(k), list) else list(v)) for k, v in DEFAULT.items()}
        return out

    def _save(self, d):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)

    def tool_allowed(self, name, caps=None):
        caps = caps or self.load()
        return name not in NEVER_TOOLS and any(fnmatch.fnmatchcase(name, p) for p in caps["mcp_tools"])

    def allowed_tools_flag(self, caps=None):
        """the --allowedTools value for Claude Code: the shell entries plus its file tools plus the MCP tools of the gateway server"""
        caps = caps or self.load()
        return " ".join(["Read", "Edit", "Write", "Glob", "Grep"] + ["Bash(%s)" % s for s in caps["shell"]] + ["mcp__agentdispatch"])

    # ---------------------------------------------------------------- asking for more
    def _check(self, kind, value):
        """-> ("auto" | "owner" | "refuse", reason)"""
        if kind not in KINDS:
            return "refuse", "kind must be one of %s" % ", ".join(KINDS)
        v = (value or "").strip()
        if not v or len(v) > 80:
            return "refuse", "give one name of at most 80 characters"
        if kind == "mcp_tool":
            if v in NEVER_TOOLS:
                return "refuse", "%s is the owner's: agents never get it" % v
            if v not in set(self.tool_names()):
                return "refuse", "there is no MCP tool called %s (see tool_list / the board's tools)" % v
            return ("auto", "a read-only tool that exists") if self.read_only(v) else ("owner", "it can change things: the owner decides")
        if kind == "context":
            return ("auto", "a known context pack") if v in PACKS else ("refuse", "unknown pack; known: %s" % ", ".join(sorted(PACKS)))
        base = re.sub(r":\*$", "", v).split(" ")[0]
        if not re.match(r"^[A-Za-z0-9_.-]+(?: [A-Za-z0-9_.-]+)?:\*$", v):
            return "refuse", "a shell entry looks like  command:*  or  git log:*"
        if base in DENY_SHELL:
            return "refuse", "%s is on the deny list (network, privilege or process control)" % base
        return "auto", "a plain command; the container has no network and only the job's workspace"

    def request(self, kind, value, why, by):
        """-> dict(ok, status: added | pending | refused | already, id?, reason)"""
        verdict, reason = self._check(kind, value)
        with self.lock:
            d = self.load()
            field = {"mcp_tool": "mcp_tools", "shell": "shell", "context": "context"}.get(kind)
            if verdict == "refuse":
                return dict(ok=False, status="refused", reason=reason)
            if value in d[field]:
                return dict(ok=True, status="already", reason="it is already on the list")
            entry = dict(id="C-%d" % (len(d["proposals"]) + 1), kind=kind, value=value, why=str(why or "")[:300], by=by, t=self.now(), reason=reason)
            if verdict == "auto":
                d[field].append(value)
                entry["status"] = "added"
            else:
                entry["status"] = "pending"
            d["proposals"].append(entry)
            self._save(d)
        return dict(ok=True, status=entry["status"], id=entry["id"], reason=reason)

    def decide(self, pid, approve, by="owner"):
        with self.lock:
            d = self.load()
            p = next((x for x in d["proposals"] if x["id"] == pid), None)
            if not p or p["status"] != "pending":
                return dict(ok=False, error="not_found", message="no pending proposal %s" % pid)
            p["status"], p["decided_by"] = ("added" if approve else "denied"), by
            if approve:
                field = {"mcp_tool": "mcp_tools", "shell": "shell", "context": "context"}[p["kind"]]
                if p["value"] not in d[field]:
                    d[field].append(p["value"])
            self._save(d)
        return dict(ok=True, proposal=p)

    def tools(self):
        """the MCP tools an agent uses to see and extend its own list"""
        from ..mcp_server import Tool
        S = lambda n: dict(type="string", maxLength=n)
        return [
            Tool("capability_list", "What you may use: the broker tools, the shell commands and the context files you were given, and proposals waiting for the owner.", dict(type="object", properties={}, required=[]),
                 lambda a, s: self.listing(), ("full",), read_only=True),
            Tool("capability_request", "Ask for something you need and do not have: kind=mcp_tool (a broker tool by name), shell (a command such as 'mypy:*'), or context (a pack of files). Safe requests are granted at once and "
                 "stay for every later run; others wait for the owner.", dict(type="object", properties=dict(kind=dict(type="string", enum=list(KINDS)), value=S(80), why=S(300)), required=["kind", "value", "why"]),
                 lambda a, s: self.request(a["kind"], a["value"], a.get("why", ""), (s or {}).get("actor") or "agent"), ("full",)),
        ]

    def listing(self):
        d = self.load()
        return dict(ok=True, mcp_tools=d["mcp_tools"], shell=d["shell"], context=d["context"], pending=[p for p in d["proposals"] if p["status"] == "pending"],
                    how_to_add="capability_request(kind=mcp_tool|shell|context, value, why): safe requests are added at once, others wait for the owner")
