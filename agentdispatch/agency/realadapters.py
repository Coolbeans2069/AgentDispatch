"""the real adapters: run an agent CLI headless with NO tools (text in, text out) or the local model through the router, and turn its answer into a patch for the workspace copy, a plan, or a
review verdict. Each kind of work is split into REQUEST (build the prompt) and APPLY (read the answer), so the same logic serves agents the agency launches (push) and agents that ask for work and
hand back an answer (pull: pull agent, any open session). Nothing here touches a real repo."""
import json, os, re, shlex, shutil, subprocess, threading, time

from ..jsonx import extract_json
from ..textfit import fit, tail as _mtail
from ..nightshift import is_protected, kill_tree
from . import prompts as PR
from .adapters import Adapter, Result, detect_limit
from .modelbook import NOT_FOUND
from . import llmcall as LLMCALL
from .profiles import guide as PROF_GUIDE
from .jobs import is_code
from .workspace import Workspace, WorkspaceError

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
NOWIN = 0x08000000 | 0x00000200 if os.name == "nt" else 0
BUDGET = {"local": 60000, "free": 60000, "cheap": 60000, "plan": 80000, "premium": 120000}               # characters of project files shown in a prompt (quality over speed: the whole relevant program)


TOOL_TEXT_CAP = 40000                                                                             # a planned tool step's output, as later steps and the reviewer see it (head + tail with a marker past this)
TOOL_OUT, TOOL_ERR = 12000, 4000                                                                  # one tool call's stdout / stderr sent back to the model that asked for it (the end, with a marker, past this)
REPLY_SHOWN = 6000                                                                                # how much of a rejected or failed reply the next try is shown (head + tail, marked)
CONTINUE_ROUNDS = 2                                                                               # a reply cut off inside its JSON object is continued this many times before it counts as a failure

CONFIG_FAULT = re.compile(r"unrecognized_model|unrecognized model|model_not_found|model .{0,60}not found|unknown model|invalid model|is not installed|could not start |"
                          r"no local model is usable|not logged in|please (run )?/?login|invalid api key|authentication_error|cannot connect to the docker daemon|"
                          r"docker daemon is not running|error during connect|no such image|pull access denied|permission denied while trying to connect", re.I)


def agent_fault(err):
    """-> the error marked 'agent_fault: ...' when it is about the agent's own set-up (a model name it does not know, a CLI that is missing or logged out, Docker down), not about the step:
    the dispatcher cools the agent down and gives the step back without using an attempt"""
    err = err or ""
    if err.startswith("agent_fault:") or err.startswith("declined:") or detect_limit(err):
        return err
    return "agent_fault: " + err if CONFIG_FAULT.search(err) else err


def _tail(text, n=400):
    return ANSI.sub("", text or "").strip()[-n:]


def _shown(text, n=REPLY_SHOWN):
    """a reply or log as a later prompt shows it: cleaned, whole when it fits, else head + tail with a visible marker (never a silent cut)"""
    return fit(ANSI.sub("", text or "").strip(), n, "reply")


def _tool_out(r):
    return dict(output=_mtail(r.get("stdout") or "", TOOL_OUT, "tool output"), errors=(_mtail(r.get("stderr") or "", TOOL_ERR, "tool errors") if not r.get("ok") else ""))


def json_unclosed(text):
    """-> True when the reply opens a JSON object and stops before closing it (the model ran out of output room): it is worth asking for the rest.
    String-aware brace count over the text from the first `{`; fenced or prose-led replies are handled because only the object itself is scanned."""
    t = (text or "").rstrip()
    i = t.find("{")
    if i < 0:
        return False
    depth, in_s, esc = 0, False, False
    for ch in t[i:]:
        if in_s:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_s = False
        elif ch == '"':
            in_s = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return False                                                                     # a complete object (whatever follows it)
    return depth > 0


PROMPT_ARGS = ("-p", "--single", "--prompt")


def cli_argv(run, agent_id=""):
    """the agent's argv with a command-line prompt moved into a file where the CLI can read one: Grok's `-p {prompt}` becomes `--prompt-file {prompt_file}` (Windows cuts a command line
    at about 32,000 characters, so the prompt had to be squeezed to 24,000; `grok --prompt-file` was verified on 2026-10-25 with a 44,648-character prompt, head and tail both read).
    Applies to an old data/agents.json too. run["prompt_file"] = false keeps the argv as it is."""
    argv = list(run.get("argv") or [])
    if run.get("prompt_file") is False or "{prompt_file}" in argv:
        return argv
    exe = os.path.basename(argv[0]).lower() if argv else ""
    if exe.startswith("grok") and "{prompt}" in argv:
        k = argv.index("{prompt}")
        if k > 0 and argv[k - 1] in PROMPT_ARGS:
            return argv[:k - 1] + ["--prompt-file", "{prompt_file}"] + argv[k + 1:]
    return argv


def tool_round_prompt(base, text, outs, last=False):
    """the same task again with the model's tool calls and their results (whole JSON: it is never cut mid-string any more; each output is already capped with a marker)"""
    return ("%s\n\nYOUR PREVIOUS REPLY (tool calls):\n%s\n\nTOOL RESULTS (computed on this computer):\n%s\n\nNow give your final answer in the required format%s." %
            (base, _shown(text, 8000), json.dumps(outs, ensure_ascii=False), " (no more tool rounds)" if last else ""))


def plan_request_for(adapter, job, agent):
    """adapter.plan_request(job, agent) - or (job) for an adapter whose plan_request does not take the agent (a test double, an older adapter)"""
    import inspect
    fn = adapter.plan_request
    try:
        takes = len([p for p in inspect.signature(fn).parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]) >= 2 or \
            any(p.kind == p.VAR_POSITIONAL for p in inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        takes = False
    return fn(job, agent) if takes else fn(job)


def prompt_limit(agent):
    """-> the hard prompt size of an agent (characters) or None. A prompt sent on stdin or in a file has none (grok's old 24000-character argv cap
    does not survive its move to --prompt-file); a configured max_prompt_chars still counts when the prompt goes on the command line or the argv is not given;
    a {prompt} on a Windows command line is held under its 32767-character limit"""
    run = (agent or {}).get("run") or {}
    if run.get("argv"):
        on_line = "{prompt}" in " ".join(cli_argv(run, (agent or {}).get("id", "")))
        if not on_line:
            return None
        return run.get("max_prompt_chars") or (30000 if os.name == "nt" else None)
    return run.get("max_prompt_chars") or None

def _per_thread(name, default=None):
    """per-call state (the hint going in, the model / usage coming out) belongs to the worker thread that made the call: several agents run at once"""
    key = "_tl_" + name

    def get(self):
        return getattr(self._tl, name, default)

    def put(self, v):
        setattr(self._tl, name, v)
    return property(get, put)


class RealAdapter(Adapter):
    MAX_TOOL_ROUNDS = 3
    last_model, last_rung, last_usage, hint = (_per_thread(n) for n in ("last_model", "last_rung", "last_usage", "hint"))
    MAX_CALLS_PER_ROUND = 4

    def __init__(self, work_root, chat=None, which=shutil.which, popen=subprocess.Popen, toolbox=None, governor=None, book=None):
        """chat(prompt) -> text for the local model (via the router); which / popen are injectable for tests. `toolbox` lets a model's answer ask this computer to run a calculation / a Lean check /
        a test and get the output back (the work is done locally, only the short result goes to the model); `governor` records the structured result a model reports and the input hashes"""
        self.work_root, self.chat, self.which, self.popen = work_root, chat, which, popen
        self.toolbox, self.governor, self.book = toolbox, governor, book
        self._tl = threading.local()
        self.last_model = None                                                      # which concrete model answered the last call (the router says; a CLI agent is its own model)
        self.last_rung = None
        self.last_usage = None                                                      # {model, chars_in, chars_out} of the last call: the dispatcher reports it to the engine room
        self.agentic, self.digest = None, None                                      # the container runner (injectable) and digest(job) -> text: the notice board as the agent sees it
        self.hint = None                                                            # {difficulty, kind, attempts}: set by the dispatcher before a call so the router can choose a cheap rung for an easy task

    # ------------------------------------------------------------ asking an agent (push)
    def _ask(self, agent, prompt, cwd):
        """-> (text, error), with a reply that was cut off inside its JSON object continued (CONTINUE_ROUNDS times at most): the model is shown its unfinished reply and asked for exactly the rest"""
        text, err = self._ask_once(agent, prompt, cwd)
        rounds = 0
        while not err and rounds < CONTINUE_ROUNDS and json_unclosed(text) and extract_json(text, None) is None:
            rounds += 1
            more, err2 = self._ask_once(agent, self.continue_prompt(prompt, text), cwd)
            if err2 or not (more or "").strip():
                break
            text = LLMCALL.join(text, more)
        return text, err

    def continue_prompt(self, prompt, partial):
        return LLMCALL.continue_prompt(prompt, partial)

    def _ask_once(self, agent, prompt, cwd):
        """-> (text, error). An error is a failed run (non-zero exit, timeout, missing program); a limit message comes back as text+error so the dispatcher can detect it."""
        run = agent["run"]
        prompt = (prompt or "").replace("\x00", "")                                  # a NUL in a command line (or any prompt) kills the run: "embedded null character"
        if run.get("via") == "router":
            if not self.chat:
                return "", "no router available for the local model"
            try:
                pinned = run.get("model") or None                                  # openrouter / gemini / other pinned ids; auto* means undecided
                lm = None if pinned in (None, "", "auto", "auto-local", "auto-cloud") else pinned
                agency_router = getattr(self.book, "router", None) if self.book else None
                if agency_router and agency_router.ladder(agent):                   # prefer the cheapest usable rung of this agent's ladder
                    from .routing import key_of
                    need = 0.5
                    h = self.hint or {}
                    try:
                        need = agency_router.need(h.get("kind") or "step", float(h.get("difficulty") or 0.5), int(h.get("attempts") or 0))
                    except Exception:
                        pass
                    try:
                        rung = agency_router.pick(agent, need)
                        if rung:
                            lm = key_of(rung)
                    except Exception:
                        usable = self.book.usable(agent) if self.book else []
                        if usable:
                            lm = usable[0]
                elif agency_router and (self.hint or {}).get("kind") == "code" and not (lm and ("/" in lm or lm.startswith("openrouter") or lm.endswith(":free"))):
                    lm = agency_router.local_model("code")                          # a coding model for code, not whatever the router likes best
                order = [lm]
                if agency_router and agency_router.ladder(agent):                    # other usable models of this agent, in order: an EMPTY reply (a reasoning model that spent its budget thinking, a model that is down) moves on to the next
                    try:
                        from .routing import key_of as _k
                        h2 = self.hint or {}
                        order += [_k(x_) for x_ in agency_router.order(agent, h2.get("difficulty", 0.5), h2.get("kind", "step"), h2.get("attempts", 0)) if _k(x_) != lm][:3]
                    except Exception:
                        pass
                empty = []
                for m_ in order:
                    try:
                        r = self.chat(prompt, m_) if m_ else self.chat(prompt)
                    except TypeError:
                        r = self.chat(prompt)
                    text_, used_ = (r[0], r[1]) if isinstance(r, tuple) else (r, m_)
                    if (text_ or "").strip():
                        self.last_model = used_
                        return text_, ""
                    empty.append(str(used_ or m_ or "default"))
                return "", "the model gave an empty reply (tried %s): it ran out of its reply budget or is not answering" % ", ".join(empty)
            except Exception as e:
                msg = "%s: %s" % (e.__class__.__name__, str(e)[:2000])
                # OpenRouter / Gemini / xAI HTTP 429 and quota errors surface here as RouteError
                code = getattr(e, "code", None)
                if code == 429 or detect_limit(msg):
                    return "", "usage limit: " + msg
                return "", msg
        if run.get("via") == "pull":
            return "", "%s takes its work by asking for it (pull); the agency cannot launch it" % agent["id"]
        router = getattr(self.book, "router", None) if self.book else None
        if router and router.ladder(agent):                                             # an agent with a ladder of models / efforts: the cheapest rung that is good enough, the next when a model runs out
            h = self.hint or {}
            tried, last, dead = [], ("", ""), set()
            for r in router.order(agent, h.get("difficulty", 0.97), h.get("kind", "step"), h.get("attempts", 0)):
                if r.get("model") in dead:
                    continue
                out, err = self._run_cli(agent, prompt, cwd, r)
                if err and detect_limit(err + " " + out):
                    if not r.get("model"):                                              # one model: the whole agent is out, the dispatcher cools it down
                        return out, err
                    dead.add(r["model"])
                    until = self.book.cool(agent, r["model"], err + " " + out)
                    tried.append("%s hit its limit (back %s)" % (r["model"], time.strftime("%H:%M", time.localtime(until))))
                    last = (out, err)
                    continue
                if err and r.get("model") and NOT_FOUND.search(err):
                    dead.add(r["model"])
                    self.book.invalid(agent, r["model"], err)
                    tried.append("%s does not exist" % r["model"])
                    last = (out, err)
                    continue
                self.last_model = (r.get("model") or "default") + ("@" + r["effort"] if r.get("effort") else "")
                self.last_rung = r
                self.last_usage = dict(model=r.get("model"), chars_in=len(prompt), chars_out=len(out or ""))
                return out, err
            if not tried:
                return "", "usage limit: every model of %s is out (%s)" % (agent["id"], "; ".join(self.book.reasons(agent))[:2000])
            return last[0], "usage limit: " + "; ".join(tried) + ". " + last[1][-2000:]
        return self._run_cli(agent, prompt, cwd, None)

    def _run_cli(self, agent, prompt, cwd, rung):
        """rung: {model, effort} from the router (None = the CLI's own default)"""
        run = agent["run"]
        argv = cli_argv(run, agent.get("id", ""))
        pfile = None
        if any("{prompt_file}" in a for a in argv):                                      # the prompt goes in a file the CLI reads (no command-line limit, nothing cut)
            import tempfile
            fd, pfile = tempfile.mkstemp(prefix="ad_prompt_", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(prompt)
        try:
            return self._run_argv(agent, run, [a.replace("{cwd}", cwd or ".").replace("{prompt_file}", pfile or "").replace("{prompt}", prompt) for a in argv], prompt, cwd, rung, bool(pfile))
        finally:
            if pfile:
                try:
                    os.remove(pfile)
                except OSError:
                    pass

    def _run_argv(self, agent, run, argv, prompt, cwd, rung, prompt_in_file=False):
        if rung:
            argv += self.book.router.argv_for(agent, rung)
        exe = self.which(argv[0])
        if not exe:
            return "", "%s is not installed" % argv[0]
        argv[0] = exe
        env = dict(os.environ, NO_COLOR="1", TERM="dumb")
        extra = [d for d in (r"C:\Program Files\nodejs", os.path.expandvars(r"%APPDATA%\npm"), os.path.expandvars(r"%USERPROFILE%\.grok\bin")) if os.path.isdir(d) and d.lower() not in env.get("PATH", "").lower()]
        if extra:                                                                       # the agent CLIs (gemini, codex) are node programs: a service started without the user's PATH must still find node
            env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        try:
            p = self.popen(argv, cwd=cwd or None, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                           creationflags=NOWIN, start_new_session=(os.name != "nt"))
        except OSError as e:
            return "", "could not start %s: %s" % (argv[0], e)
        try:
            out, err = p.communicate(input=prompt if (run.get("stdin_prompt", True) and not prompt_in_file) else None, timeout=run.get("timeout_s", 1800))
        except subprocess.TimeoutExpired:
            kill_tree(p)
            try:
                p.communicate(timeout=10)
            except Exception:
                pass
            return "", "timed out after %d s" % run.get("timeout_s", 1800)
        out = ANSI.sub("", out or "")
        if p.returncode != 0:
            return out, "exit %s: %s" % (p.returncode, _mtail(ANSI.sub("", err or out or "").strip(), 8000, "error output"))   # the end of a failed run, with a marker when its start was left out (was a silent 3000-character tail)
        return out, ""

    # ------------------------------------------------------------ steps
    def workspace(self, job):
        if not job.get("target"):
            raise WorkspaceError("this job has no target folder")
        meta = job.get("meta") or {}
        ind = meta.get("independence") or {}
        if ind.get("cutoff") and meta.get("repo"):                                  # an evidence cutoff: the project as it was at that commit, nothing newer
            from ..campaign import scope as SC
            forb = list(ind.get("forbidden_inputs") or [])
            ws, full = Workspace.ensure_git(self.work_root, job["id"], meta["repo"], ind["cutoff"], allow=lambda rel: SC.read_ok(meta, rel) and not SC.match(rel, forb), stamp=job.get("created"))
        else:
            ws = Workspace.ensure(self.work_root, job["id"], job["target"], stamp=job.get("created"))
        if self.governor:
            try:
                self.governor.record_inputs(job)
            except Exception:
                pass
        return ws

    # ------------------------------------------------------------ local tools: what a model may ask this computer to do
    def tools_text(self, step):
        if not self.toolbox:
            return ""
        have = [t for t in self.toolbox.reg.listing() if t["available"] and t["kind"] != "runtime" and t["kind"] != "lib"]
        if not have:
            return ""
        lines = ["%s: %s" % (t["id"], t["desc"]) for t in have]
        return ("LOCAL TOOLS (free, run on this computer): to use them reply ONLY with {\"tool_calls\": [{\"tool\": \"calc\", \"code\": \"<python that prints its result>\"}, "
                "{\"tool\": \"lean\", \"code\": \"<Lean 4 source>\"}, {\"tool\": \"git\", \"args\": [\"log\", \"-1\"]}]} (at most %d calls a round, %d rounds); you will be sent the outputs and then give your final "
                "answer in the format below. Prefer a tool to doing arithmetic or checking a proof in your head. Tools: %s" % (self.MAX_CALLS_PER_ROUND, self.MAX_TOOL_ROUNDS, " | ".join(lines)))

    def _tool_calls(self, text):
        d = extract_json(text, "tool_calls")
        if not isinstance(d, dict) or not isinstance(d.get("tool_calls"), list) or any(k in d for k in ("files", "answer", "steps", "approve")):
            return None
        out = []
        for c in d["tool_calls"]:
            if not isinstance(c, dict):
                continue
            fn = c.get("function") if isinstance(c.get("function"), dict) else {}
            name = c.get("tool") or c.get("tool_name") or c.get("name") or fn.get("name")
            params = c.get("args") if c.get("args") is not None else c.get("parameters") if c.get("parameters") is not None else c.get("arguments") if c.get("arguments") is not None else fn.get("arguments")
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except ValueError:
                    params = {"code": params} if name == "calc" else None
            if not isinstance(name, str):
                continue
            call = dict(tool=name)
            if isinstance(params, dict):                                                    # {"path": "x"} / {"args": [...]} / {"code": "..."}: map onto what the toolbox takes
                for k_ in ("code", "file", "cwd", "timeout", "project"):
                    if params.get(k_) is not None:
                        call[k_] = params[k_]
                if isinstance(params.get("args"), list):
                    call["args"] = [str(x_) for x_ in params["args"]]
                elif params.get("path") and name in ("read_file",):
                    call["file"] = str(params["path"])
                    call["args"] = [str(params["path"])]
            elif isinstance(params, list):
                call["args"] = [str(x_) for x_ in params]
            elif c.get("code") or c.get("file") or c.get("args"):
                call.update({k_: c[k_] for k_ in ("code", "file", "args") if c.get(k_)})
            out.append(call)
        return out[:self.MAX_CALLS_PER_ROUND] or None

    def _ask_with_tools(self, agent, prompt, cwd, job):
        """ask; while the answer is a request for tools, run them here and ask again with the outputs (the model only ever sees the short results)"""
        text, err = self._ask(agent, prompt, cwd)
        rounds, transcript = 0, prompt
        while not err and self.toolbox and rounds < self.MAX_TOOL_ROUNDS:
            calls = self._tool_calls(text)
            if not calls:
                break
            rounds += 1
            outs = []
            for c in calls:
                r = self.toolbox.run(c["tool"], args=c.get("args"), code=c.get("code"), file=c.get("file"), cwd=cwd, timeout=c.get("timeout"), caller=agent["id"], job=job["id"], project=c.get("project"))
                outs.append(dict(tool=c["tool"], ok=r.get("ok"), problems=r.get("problems"), secs=r.get("secs"), **_tool_out(r)))
            transcript = self.tool_round_prompt(prompt, text, outs, rounds >= self.MAX_TOOL_ROUNDS)
            text, err = self._ask(agent, transcript, cwd)
        return text, err

    def tool_round_prompt(self, base, text, outs, last=False):
        return tool_round_prompt(base, text, outs, last)

    CANT = re.compile(r"\b(cannot|can't|can not|could not|couldn't|unable to|not able to|no way to|do not have (?:a |the )?(?:ability|access|tool))\b", re.I)

    def declined(self, text):
        """-> the agent's reason when its answer is an honest can't-do (no files, notes that say it cannot), else ''"""
        d = extract_json(text or "", None)
        if isinstance(d, dict) and not d.get("files") and not d.get("tool_calls"):
            notes = str(d.get("notes") or d.get("answer") or d.get("reason") or "")
            if notes and self.CANT.search(notes):
                return notes
        return ""

    def run_tool_calls(self, calls, job, caller):
        """run an agent's tool_calls here (in the job's workspace copy) -> the capped results to send back; used for agents that answer by asking (pull agents), like a launched agent's loop does"""
        cwd = None
        if self.toolbox and job.get("target") and is_code(job):
            try:
                cwd = self.workspace(job).project
            except (WorkspaceError, OSError):
                cwd = None
        outs = []
        for c in (calls or [])[:self.MAX_CALLS_PER_ROUND]:
            r = self.toolbox.run(c["tool"], args=c.get("args"), code=c.get("code"), file=c.get("file"), cwd=cwd, timeout=c.get("timeout"), caller=caller, job=job["id"], project=c.get("project"))
            outs.append(dict(tool=c["tool"], ok=r.get("ok"), problems=r.get("problems"), secs=r.get("secs"), **_tool_out(r)))
        return outs

    def repair_prompt(self, prompt, bad_text, problem):
        head = (bad_text or "").strip()
        why = "was EMPTY (the reply was cut off or contained only thinking: answer directly, without thinking out loud)" if not head else "was:\n<<<REPLY\n" + _shown(head, 4000) + "\nREPLY>>>"
        return (prompt + "\n\n=== YOUR PREVIOUS REPLY WAS REJECTED ===\nProblem: %s\nYour reply %s\nReply again NOW with ONLY the required JSON object: first character `{`, last character `}`, nothing else. "
                "Give it complete (every file that must change, each in full as one JSON string); leave out anything that was not asked for." % (problem, why))

    def _run_tool_step(self, step, job):
        """a deterministic step: the computer runs the tool call itself (no model, no tokens)"""
        spec = (step.get("spec") or {}).get("tool") or {}
        if not self.toolbox or not spec.get("tool"):
            return Result(ok=False, error="this step has no tool call to run (a deterministic step needs {tool, args | code | file})")
        cwd, ws = spec.get("cwd"), None
        if job.get("target") and is_code(job):
            try:
                ws = self.workspace(job)
                cwd = cwd or ws.project
            except (WorkspaceError, OSError):
                ws = None
        file = spec.get("file")
        if file and ws and not os.path.isabs(file):
            file = os.path.join(ws.project, file)
        r = self.toolbox.run(spec["tool"], args=spec.get("args"), code=spec.get("code"), file=file, cwd=cwd, timeout=spec.get("timeout"), caller="local-tools", job=job["id"], project=spec.get("project"))
        want = "fail" if str(spec.get("expect", "pass")).lower() == "fail" else "pass"
        got = "pass" if r.get("ok") else "fail"
        refused = bool(r.get("problems")) and r.get("rc") is None and not r.get("sorry")
        missing_input = any(("is not a file" in str(x_)) or ("does not exist" in str(x_)) for x_ in (r.get("problems") or []))      # a negative probe on a file that is not there is answered by the refusal itself
        ok = got == want and not (refused and want == "fail" and not missing_input)
        text = ("%s %s (expected %s) in %.1fs, output sha256 %s\n%s" % (spec["tool"], got, want, r.get("secs") or 0, (r.get("sha256") or "")[:12], fit((r.get("stdout") or "") + ("\n" + "; ".join(r.get("problems") or []) if r.get("problems") else ""), TOOL_TEXT_CAP, "tool output"))).strip()
        return Result(ok=ok, text=text, error="" if ok else ("tool %s %s but %s was expected: %s" % (spec["tool"], got, want, _mtail((r.get("stderr") or "") + "; ".join(r.get("problems") or []), 3000, "tool errors"))))

    def _ctx(self, job):
        ws, check_argv, chk = None, None, None
        if is_code(job):
            ws = self.workspace(job)
            check = (job.get("acceptance") or {}).get("check")
            if check:
                check_argv = shlex.split(check) if isinstance(check, str) else list(check)
                hc = (job.get("acceptance") or {}).get("host_check")
                hscript = [a for a in (shlex.split(hc) if hc else []) if a.endswith(".py")]
                chk = {"command": check_argv, "protect": hscript}                      # the host check's script is protected like the check itself
        return ws, check_argv, chk

    def step_request(self, agent, step, job):
        """-> the prompt for one step (creates the workspace copy the first time)"""
        ws, check_argv, chk = self._ctx(job)
        lim = prompt_limit(agent)
        earlier = self.earlier_budget(agent)
        if ws:
            prio = [a for a in (check_argv or []) if a.endswith(".py")]
            budget = BUDGET.get(agent["cost"], 24000)
            if lim:                                                                      # e.g. Grok takes the prompt as a command-line argument (Windows limit about 32,000 characters)
                used = len(PR.earlier_results(job, step, total=earlier))                 # only what the earlier steps really take: a first step keeps the whole file budget
                budget = min(budget, max(2000, lim - 6000 - used))
            from ..campaign import scope as SC
            meta = job.get("meta") or {}
            hidden = [p for p in ws.files() if not SC.read_ok(meta, p)] if (meta.get("scope") or {}).get("read") or (meta.get("scope") or {}).get("forbidden") else []
            tree, picked = ws.context(step["objective"], budget, priority=prio, skip=set(hidden))
        else:
            tree, picked = [], []
        return self.guided(agent, "step", PR.step_prompt(job, step, tree, picked, tools_text=self.tools_text(step), hint=(step.get("spec") or {}).get("hint", ""), earlier_total=earlier))

    def guided(self, agent, kind, prompt):
        """the agent's own prompting notes (agent_profiles.json "prompt": how it is run, what it can reach, how long to answer) put in front of the task; the task and its strict reply format stay last"""
        g = PROF_GUIDE(agent, kind, getattr(self, "profiles_dir", None))
        return (g + "\n\n" + prompt) if g else prompt

    def earlier_budget(self, agent):
        """how much of the earlier steps' results an agent is shown: the full allowance, less for a prompt that has a hard size limit"""
        lim = prompt_limit(agent)
        return min(PR.EARLIER_TOTAL, max(3000, lim // 4)) if lim else PR.EARLIER_TOTAL

    def step_apply(self, agent, step, job, text):
        """-> Result: read an agent's answer to a step and write it into the workspace copy"""
        if detect_limit(text) and not text.lstrip().startswith("{"):
            return Result(ok=False, text=_shown(text), limit_hit=True, error="limit message in the answer")
        if self.governor and (job.get("meta") or {}).get("campaign"):
            try:
                self.governor.parse_and_record(job["id"], step.get("n", 0), text)
            except Exception:
                pass
        if not is_code(job):
            ans, notes = PR.parse_answer(text)
            if not ans and self.declined(text):                                                # {"answer": "", "notes": "I cannot do this: ..."} is a hand-off, as it is for code
                return Result(ok=False, text=_shown(text), error="declined: " + self.declined(text))
            return Result(ok=True, text=ans) if ans else Result(ok=False, text=_shown(text), error="the answer had no usable {\"answer\": ...} object")
        try:
            ws, check_argv, chk = self._ctx(job)
        except (WorkspaceError, OSError) as e:
            return Result(ok=False, error=str(e))
        files, notes, problem = PR.parse_patch(text, check_argv)
        if problem:
            why = self.declined(text)
            if why:                                                                  # an explicit "I cannot do this" is a hand-off to another agent, not a failed attempt
                return Result(ok=False, text=_shown(text), error="declined: " + why)
            return Result(ok=False, text=_shown(text), error=problem)
        touched, lines = [], 0
        from ..campaign import scope as SC
        for rel in files:
            ok, why = SC.write_ok(job.get("meta") or {}, rel)
            if not ok:
                return Result(ok=False, error="scope: " + why, text=_tail(notes))
        try:
            for rel, content in files.items():
                lines += ws.write(rel, content, protected=(lambda r: is_protected(r, chk)) if chk else (lambda r: False))
                touched.append(rel)
        except WorkspaceError as e:
            return Result(ok=False, error=str(e), text=_tail(notes))
        return Result(ok=True, text=notes or "changed %d file(s)" % len(touched), touched=touched, diff_lines=lines)

    def _run_agentic(self, agent, step, job):
        """a local model as an agent: it works inside its own container (the project copy at /work, the notice board / playbooks / local tools through the gateway); what it touched is policed, then the
        normal check and review apply. A job with no project folder is a text job: the agent's final message is the answer."""
        from .agentic import AgenticRunner, prompt_for
        from ..campaign import scope as SC
        meta = job.get("meta") or {}
        code = is_code(job) and bool(job.get("target"))
        ws, check_argv, chk, scratch = None, None, None, None
        if code:
            try:
                ws, check_argv, chk = self._ctx(job)
            except (WorkspaceError, OSError) as e:
                return Result(ok=False, error=str(e))
        sc = meta.get("scope") or {}
        scope_text = ("YOU MAY WRITE ONLY: %s. NEVER TOUCH: %s." % (", ".join(sc.get("write") or ["(anything in the project)"]), ", ".join(sc.get("forbidden") or ["(nothing listed)"]))) if (sc.get("write") or sc.get("forbidden")) else ""
        failed = step.get("evidence") if step.get("attempts") and str(step.get("evidence") or "").startswith("FAILED") else ""
        router = getattr(self.book, "router", None) if self.book else None
        h = self.hint or {}
        rungs = router.order(agent, h.get("difficulty", 0.5), "code" if code else "step", h.get("attempts", 0)) if router else []
        if not rungs:
            return Result(ok=False, error="agent_fault: no local model is usable for %s right now" % agent["id"])
        digest = self.digest(job) if self.digest else ""
        runner = self.agentic or AgenticRunner(popen=self.popen, which=self.which)
        prompt = self.guided(agent, "step", prompt_for(job, step, check_argv, scope_text, failed, bool(getattr(runner, "env", None)) or bool(digest)))   # its own notes (agent_profiles.json) first, like every other agent
        if code:
            project = ws.project
        else:
            import tempfile
            scratch = project = tempfile.mkdtemp(prefix="agent_text_")
        try:
            out, err = runner.run(project, prompt, rungs[0]["model"], job=job, step=step, failed=failed, digest_text=digest, timeout=agent["run"].get("timeout_s", 1800), argv=agent["run"].get("argv"), agent_id=agent["id"])
        finally:
            if scratch:
                shutil.rmtree(scratch, ignore_errors=True)
        self.last_model = rungs[0]["model"]
        if err:
            if NOT_FOUND.search(err) and self.book:
                self.book.invalid(agent, rungs[0]["model"], err)
            if detect_limit(err):
                return Result(ok=False, text=_shown(out), error=err, limit_hit=True)
            return Result(ok=False, text=_shown(out), error=agent_fault(err))
        if self.governor and meta.get("campaign"):                                      # the structured result a campaign job reports travels in the final message
            try:
                self.governor.parse_and_record(job["id"], step.get("n", 0), out)
            except Exception:
                pass
        if not code:
            return Result(ok=True, text=out.strip()) if out.strip() else Result(ok=False, error="the agent gave no answer")
        touched, bad = runner.police(ws, chk, lambda rel: SC.write_ok(meta, rel))
        if bad:
            return Result(ok=False, text=_shown(out), error="the agent changed what it must not: " + "; ".join(bad), touched=touched)
        if not touched:
            return Result(ok=False, text=_shown(out), error="the agent finished without changing any file")
        return Result(ok=True, text=_shown(out, 20000) or "changed %d file(s)" % len(touched), touched=touched, diff_lines=ws.diff_lines())

    def run(self, agent, step, job, workspace=None):
        if (agent.get("run") or {}).get("via") == "tools":
            return self._run_tool_step(step, job)
        if (agent.get("run") or {}).get("via") == "agentic":
            return self._run_agentic(agent, step, job)
        try:
            prompt = self.step_request(agent, step, job)
        except (WorkspaceError, OSError) as e:
            return Result(ok=False, error=str(e))
        ws = self.workspace(job) if is_code(job) else None
        text, err = self._ask_with_tools(agent, prompt, ws.project if ws else None, job)
        if err:
            hit = detect_limit(err + " " + (text or ""))
            return Result(ok=False, text=_shown(text), error=err if hit else agent_fault(err), limit_hit=hit)
        res = self.step_apply(agent, step, job, text)
        if not res.ok and not res.limit_hit and any(w in (res.error or "") for w in ("no usable", "not path -> text", "usable {")) and not res.error.startswith("declined:"):
            fix = self.repair_prompt(prompt, text, res.error)                                # the format was wrong, not the work: ask once more with the exact complaint, before an attempt is used
            text2, err2 = self._ask_with_tools(agent, fix, ws.project if ws else None, job)
            if not err2:
                res2 = self.step_apply(agent, step, job, text2)
                if res2.ok or res2.error.startswith("declined:"):
                    return res2
        return res

    # ------------------------------------------------------------ planning and review
    def plan_request(self, job, agent=None):
        tree = []
        if is_code(job) and job.get("target"):
            try:
                tree = self.workspace(job).files()
            except (WorkspaceError, OSError):
                tree = []
        agents = ""
        fn = getattr(self, "profiles_fn", None)                                                 # set by the dispatcher: the live AGENTS block (capabilities, availability, success rates)
        if fn:
            try:
                agents = fn() or ""
            except Exception:
                agents = ""
        return self.guided(agent, "plan", PR.plan_prompt(job, tree, agents))

    def plan_apply(self, text):
        steps = PR.parse_steps(text)
        if not steps:
            raise RuntimeError("the planner's answer had no usable {\"steps\": [...]} object")
        return steps

    def plan(self, agent, job):
        prompt = plan_request_for(self, job, agent)
        text, err = self._ask(agent, prompt, None)
        if err:
            raise RuntimeError(err)
        steps = self.plan_apply(text)
        check = getattr(self, "plan_check_fn", None)                                            # the dispatcher's plan validation: needs an agent has, no host-only files
        problems = check(steps, job) if check and steps else []
        if problems:                                                                            # told once, exactly what is wrong, and asked again
            from .jobs import NEEDS
            text2, err2 = self._ask(agent, prompt + "\n\nYOUR PLAN WAS REFUSED:\n- " + "\n- ".join(problems) + "\nThe full list of needs is: " + ", ".join(NEEDS)
                                    + ". Fix these and reply again with the whole plan in the same JSON shape.", None)
            if err2:
                raise RuntimeError(err2)
            steps = self.plan_apply(text2)
            problems = check(steps, job) if steps else []
            if problems:
                raise RuntimeError("plan refused after one re-ask: " + "; ".join(problems))
        return steps

    REVIEW_CHARS = {"free": 60000, "local": 60000, "cheap": 80000, "plan": 100000, "premium": 160000}

    def review_budget(self, agent):
        """how much of a change a reviewer is shown: what its prompt can take, by its class (a reviewer that cannot see the code cannot review it)"""
        if not agent:
            return self.REVIEW_CHARS["free"]
        n = self.REVIEW_CHARS.get(agent.get("cost", "plan"), self.REVIEW_CHARS["plan"])
        lim = prompt_limit(agent)
        return max(4000, min(n, lim - 8000)) if lim else n

    def review_request(self, job, results, agent=None):
        diff = ""
        if is_code(job) and job.get("target"):
            used = len(PR.earlier_results(job, dict(n=10 ** 6), total=self.earlier_budget(agent))) if prompt_limit(agent) else 0
            diff, cut = self.workspace(job).diff_budget(self.review_budget(agent) - used)          # a limited prompt gives up only the room the steps' results really take
            if cut:
                diff += ("\n\nTHESE FILES WERE TOO LONG TO SHOW YOU IN FULL: %s.\nDo not approve or reject on what you could not see: say exactly which parts you could not check, judge only what is shown, "
                         "and rely on the acceptance check for the rest." % "; ".join(cut))
        res = results or {}
        if not diff and not is_code(job):
            diff = fit("\n".join("STEP %s (%s): %s" % (s["n"], s["agent"], fit(s.get("evidence") or "", PR.EARLIER_PER, "step result")) for s in job.get("steps", [])), self.review_budget(agent), "step results")
            earlier = ""
        else:
            earlier = PR.earlier_results(job, dict(n=10 ** 6), total=self.earlier_budget(agent))                               # a code review also sees what the steps' tools really printed
        return self.guided(agent, "review", PR.review_prompt(job, diff, res.get("check_ok"), res.get("check_out", ""), earlier))

    def review_apply(self, text):
        return PR.parse_verdict(text)

    def review(self, agent, job, results):
        prompt = self.review_request(job, results, agent)
        text, err = self._ask(agent, prompt, None)
        if err:
            raise RuntimeError(err)
        verdict = self.review_apply(text)
        if verdict.get("reasons") == [PR.INVALID_VERDICT]:                                # the reply was not a verdict at all: re-ask once with the exact complaint, then it is the reviewer's failure, never a rejection of the work
            text, err = self._ask(agent, self.repair_prompt(prompt, text, "it was not a valid verdict"), None)
            if err:
                raise RuntimeError(err)
            verdict = self.review_apply(text)
            if verdict.get("reasons") == [PR.INVALID_VERDICT]:
                raise RuntimeError("the reviewer %s gave no valid verdict" % agent.get("id", "?"))
        return verdict
