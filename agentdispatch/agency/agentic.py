"""a local model as a real agent: Claude Code (the CLI) pointed at Ollama, running in its own Docker container with ONLY the job's workspace copy mounted at /work.
  Network: the container is firewalled (agent_entry.sh) to one address, the agency gateway (gateway.py). Through it the agent reaches exactly two things: its own model (the one the router chose) and the
  broker's MCP tools on the capability list (notice board, playbooks, local tools, campaign state; tool_run is pinned to the run's own workspace). No key or token is in the container.
  Context: /board (read-only) holds a pack written before the run - BRIEF.md (the house rules and workflow), job.json (goal, acceptance, scope, gates, expected outputs, what failed before), digest.md (what
  every agent is doing + what the board knows), playbooks.md (similar work that succeeded / failed), tools.md (the local tools), schemas.md (result / meta formats).
  Window: Ollama's context for the model is pinned (a derived model with num_ctx) and Claude Code is told the same number, so nothing is silently truncated and VRAM is predictable.
Everything the agent does lands in the throw-away copy; the agency then checks what it touched (protected files, scope, binaries are reverted and fail the step) and the normal acceptance check, review and
apply gate take over."""
import json, os, re, shutil, subprocess, tempfile, time, urllib.request
from ..textfit import fit as _fit, tail as _tail

from ..nightshift import is_protected, kill_tree
from .prompts import earlier_results

IMAGE = "ad-agent:2"
DEFAULT_CTX = 65536
BINARY_EXT = (".exe", ".dll", ".so", ".bin", ".zip", ".png", ".jpg", ".pdf", ".pyc", ".sqlite", ".db")
NOWIN = 0x08000000 if os.name == "nt" else 0
BRIEF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_brief.md")
SYSTEM_APPEND = ("You are a member of an automated engineering team and work inside a sandbox. First read /board/BRIEF.md and /board/job.json: they hold the rules, your job, the expected outputs and what the other agents "
                 "are doing. Use the MCP server 'agentdispatch' (tools named mcp__agentdispatch__*) for the notice board, playbooks and local tools whenever they help; ask for a missing tool with tool_need or a missing permission with capability_request.")


def prompt_for(job, step, check, scope_text, failed, has_pack):
    acc = job.get("acceptance") or {}
    check_s = " ".join(check) if isinstance(check, list) else (check or "")
    code = bool(job.get("target")) and (job.get("kind") in ("code", "rebuild") or bool(check_s))
    return "\n".join([x for x in [
        ("Your project is in /work (a private copy). Make the change by editing the files there; run the checks yourself and fix what fails." if code else
         "Do the work described below and reply with the complete result as your final message (it is read as the step's answer)."),
        "Read /board/BRIEF.md and /board/job.json first." if has_pack else "",
        "JOB GOAL: " + job["goal"], "THIS STEP: " + step["objective"], scope_text,
        earlier_results(job, step),
        ("PREVIOUS ATTEMPT " + failed + "\nDo not repeat that mistake.") if failed else "",
        ("ACCEPTANCE CHECK (must exit 0; run it before you finish): " + check_s) if check_s else "", ("REVIEW RUBRIC: " + acc["rubric"]) if acc.get("rubric") else "",
        "When done reply with a short summary of what you changed and the result of the check." if code else ""] if x])


def _req(url, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw[:1] in (b"{", b"[") else {}


VALID_MODEL = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,120}(:[A-Za-z0-9._-]{1,80})?$")


def variant_name(model, ctx):
    """qwen3-coder:30b, 65536 -> qwen3-coder-ctx64k:30b; nemotron-cascade-2:latest -> nemotron-cascade-2-ctx32k:latest; an old-style or already pinned name is re-based first.
    -> None when the result is not a valid Ollama model name."""
    from .vram import base_name
    base = base_name(model or "")
    repo, _, tag = base.partition(":")
    repo = re.sub(r"-ctx\d+k$", "", repo)
    name = "%s-ctx%dk:%s" % (repo, int(ctx) // 1024, tag or "latest")
    return name if VALID_MODEL.match(name) else None


def _names(tags):
    out = set()
    for m in (tags or {}).get("models", []) or []:
        n = m.get("name") or m.get("model") or ""
        out.add(n)
        if n.endswith(":latest"):
            out.add(n[:-7])
    return out


class AgentEnv:
    """everything around one run that is not the model: the context pack, the gateway run, the pinned model window"""

    def __init__(self, app, gateway, caps, ollama="http://127.0.0.1:11434", cfg=lambda: {}, fetch=_req, watch=None):
        self.app, self.gateway, self.caps, self.ollama, self.cfg, self.fetch, self.watch = app, gateway, caps, ollama, cfg, fetch, watch

    # ---------------------------------------------------------------- the model window
    def ctx_for(self, model):
        c = self.cfg() or {}
        return int((c.get("models") or {}).get(model) or c.get("default") or DEFAULT_CTX)

    def variant(self, model):
        """-> (name of the Ollama model to run, its context in tokens): a derived model with num_ctx pinned (created once), named BASE-ctxNk:TAG (qwen3-coder-ctx64k:30b,
        nemotron-cascade-2-ctx32k:latest) so the tag stays a tag; the name is checked and only used once Ollama really lists it, otherwise the plain model runs"""
        from .vram import base_name
        model = base_name(model) or model                                                # an already pinned / old-style name: its window, its "from" and the fallback are the base model's
        ctx = self.ctx_for(model)
        if self.watch:                                                                    # the biggest window that fits the GPU memory free right now (never below 32k)
            ok, ctx, need, avail, why = self.watch.plan(model, ctx)
        name = variant_name(model, ctx)
        if not name:
            return model, ctx
        try:
            have = _names(self.fetch(self.ollama + "/api/tags"))
            if not ({name, name.split(":")[0]} & have):
                self.fetch(self.ollama + "/api/create", dict(model=name, **{"from": model}, parameters=dict(num_ctx=ctx), stream=False), timeout=600)
                have = _names(self.fetch(self.ollama + "/api/tags"))
                if not ({name, name.split(":")[0]} & have):
                    return model, ctx                                                        # Ollama did not make it: never hand a name that does not exist to the agent ("unrecognized_model")
        except Exception:
            return model, ctx                                                                # could not pin: run the plain model (Claude Code is still told the window)
        return name, ctx

    def selftest(self, models, which=shutil.which, create=False):
        """startup self-test for the local agent: is Docker there, is each model installed, is its pinned-window name valid and (when it exists or `create`) really listed by Ollama?
        -> list of dict(model, ok, run_as, why). Nothing is run in a container; nothing is pulled."""
        out = []
        docker = which("docker")
        try:
            have = _names(self.fetch(self.ollama + "/api/tags"))
            reach = ""
        except Exception as e:
            have, reach = set(), "Ollama is not answering at %s (%s)" % (self.ollama, e.__class__.__name__)
        for m in models:
            why, run_as = "", m
            name = variant_name(m, self.ctx_for(m))
            if not docker:
                why = "docker is not installed (the agent runs in a container)"
            elif reach:
                why = reach
            elif not ({m, m + ":latest", m[:-7] if m.endswith(":latest") else m} & have):
                why = "model %s is not installed in Ollama" % m
            elif not name:
                why = "no valid pinned-window name for %s" % m
            elif {name, name.split(":")[0]} & have:
                run_as = name
            elif create:
                run_as = self.variant(m)[0]
                if run_as != name:
                    why = "Ollama did not create %s" % name
            out.append(dict(model=m, ok=not why, run_as=run_as, why=why))
        return out

    # ---------------------------------------------------------------- the context pack
    def pack(self, job, step, failed, digest_text):
        d = tempfile.mkdtemp(prefix="agent_board_")
        caps = self.caps.load() if self.caps else dict(context=["digest", "job", "playbooks", "tools", "schemas"])
        want = set(caps["context"])

        def w(name, text):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(text)
        try:
            with open(BRIEF, encoding="utf-8") as f:
                w("BRIEF.md", f.read() + "\n\n## Your capability list (it can grow: capability_request)\n```json\n" + json.dumps(dict(mcp_tools=caps.get("mcp_tools"), shell=caps.get("shell"), context=caps.get("context")), indent=1) + "\n```\n")
        except OSError:
            w("BRIEF.md", "(no brief file)")
        if "job" in want:
            meta = job.get("meta") or {}
            w("job.json", json.dumps(dict(id=job.get("id"), title=job.get("title"), goal=job.get("goal"), kind=job.get("kind"), acceptance=job.get("acceptance"), step=dict(n=step.get("n"), objective=step.get("objective"), needs=step.get("needs"), attempts=step.get("attempts")),
                                          previous_failure=failed or None, meta=meta, expected_outputs=meta.get("expected_outputs") or [], budget=job.get("budget")), indent=1, default=str))      # whole: a file in the container, never cut mid-JSON
        if "digest" in want and digest_text:
            w("digest.md", digest_text)
        if "playbooks" in want:
            try:
                s = self.app.campaign.playbooks.suggest(job.get("goal") or "", job.get("kind") or "", 3)
                w("playbooks.md", "# Similar work that succeeded, and failure points seen before\n```json\n" + json.dumps(s, indent=1, default=str) + "\n```\n")
            except Exception:
                pass
        if "tools" in want:
            try:
                lines = ["- %s%s: %s" % (t["id"], "" if t.get("available") else " (not installed)", t.get("desc", "")) for t in self.app.localtools.reg.listing()]
                w("tools.md", "# Local tools on this computer (run with mcp__agentdispatch__tool_run; the computer does the work, free and exact)\n" + "\n".join(lines))
            except Exception:
                pass
        if "schemas" in want:
            from ..campaign import meta as MM
            w("schemas.md", "# Formats\n\n## Job meta (what job.json 'meta' may hold)\n```json\n" + json.dumps(MM.EXAMPLE, indent=1) + "\n```\n\n"
                            "## Result object (campaign jobs: include it in the JSON of your final message)\n"
                            '{"claim_class": "FORMAL_EXACT|CERTIFICATE_NUMERICAL|ANALYTIC_PREMISE|CODE_VERIFIED|HEURISTIC|DIAGNOSTIC", "first_open_premise": "what is still unproven or empty", '
                            '"requested_transition": {"<gate>": "<new status>"}, "values": {"<name>": "<value>"}}\nNever claim INDEPENDENTLY_CONFIRMED: it needs a second, sealed, independent job.\n')
        return d


class AgenticRunner:
    def __init__(self, popen=subprocess.Popen, which=shutil.which, image=IMAGE, env=None, gateway_port=8871):
        self.popen, self.which, self.image, self.env, self.gateway_port = popen, which, image, env, gateway_port

    def command(self, model, ctx, project, board_dir, name, token, caps_flag, port=None):
        envs = {"AD_RUN_TOKEN": token, "AD_GATEWAY_PORT": str(port or self.gateway_port), "ANTHROPIC_AUTH_TOKEN": "ollama", "ANTHROPIC_API_KEY": "", "PYTHONDONTWRITEBYTECODE": "1",
                "ANTHROPIC_SMALL_FAST_MODEL": model, "ANTHROPIC_DEFAULT_HAIKU_MODEL": model, "ANTHROPIC_DEFAULT_SONNET_MODEL": model, "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(ctx), "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(int(ctx * 0.75))}
        argv = ["docker", "run", "--rm", "-i", "--name", name, "--memory", "4g", "--cpus", "2", "--pids-limit", "512",
                "--cap-drop", "ALL", "--cap-add", "NET_ADMIN", "--cap-add", "SETUID", "--cap-add", "SETGID", "--cap-add", "SETPCAP",
                "-v", "%s:/work" % os.path.abspath(project)]
        if board_dir:
            argv += ["-v", "%s:/board:ro" % os.path.abspath(board_dir)]
        for k, v in envs.items():
            argv += ["-e", "%s=%s" % (k, v)]
        return argv + [self.image, "-p", "--model", model, "--permission-mode", "acceptEdits", "--allowedTools", caps_flag, "--mcp-config", "/tmp/mcp.json", "--strict-mcp-config",
                       "--append-system-prompt", SYSTEM_APPEND, "--no-session-persistence", "--disable-slash-commands"]

    @staticmethod
    def _activity(*dirs):
        """a cheap fingerprint of what a run has produced so far: the newest change time and the total size of the files in its folders"""
        newest, size, n = 0.0, 0, 0
        for d in dirs:
            if not d or not os.path.isdir(d):
                continue
            for root, subs, files in os.walk(d):
                subs[:] = [x for x in subs if x not in (".git", "node_modules", "__pycache__", ".venv")]
                for f in files:
                    try:
                        st = os.stat(os.path.join(root, f))
                    except OSError:
                        continue
                    newest, size, n = max(newest, st.st_mtime), size + st.st_size, n + 1
                    if n > 20000:
                        return (newest, size, n)
        return (newest, size, n)

    def run(self, project, prompt, model, job=None, step=None, failed="", digest_text="", timeout=1800, argv=None, agent_id="ollama-agent"):
        """-> (text, error). `argv` replaces the docker command (tests, or an owner who runs it some other way); it is run in the project folder."""
        board, token = None, None
        name = "adagent_%d_%d" % (os.getpid(), int(time.time() * 1000) % 10 ** 9)
        try:
            if argv:
                full, cwd = list(argv), project
            else:
                env = self.env
                caps_flag = env.caps.allowed_tools_flag() if env and env.caps else "Read Edit Write Glob Grep"
                run_model, ctx = env.variant(model) if env else (model, DEFAULT_CTX)
                board = env.pack(job or {}, step or {}, failed, digest_text) if env else None
                token = env.gateway.open_run((job or {}).get("id", ""), agent_id, run_model, project, ttl=timeout + 120) if env and env.gateway else ""
                full, cwd = self.command(run_model, ctx, project, board, name, token or "", caps_flag, getattr(env.gateway, "port", None) if env and env.gateway else None), None
            exe = self.which(full[0])
            if not exe:
                return "", "%s is not installed" % full[0]
            full[0] = exe
            env_ = dict(os.environ, MSYS_NO_PATHCONV="1", NO_COLOR="1")
            try:
                p = self.popen(full, cwd=cwd, env=env_, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=NOWIN)
            except OSError as e:
                return "", "could not start %s: %s" % (full[0], e)
            from .vram import gpu_busy
            waited, first, chunk, hard = 0, True, min(timeout, 300), max(timeout * 6, 24 * 3600)
            seen = [self._activity(project, board)]
            while True:
                try:
                    out, err = p.communicate(input=prompt if first else None, timeout=chunk)
                    break
                except subprocess.TimeoutExpired:
                    first = False
                    waited += chunk
                    now_seen = self._activity(project, board)
                    progressed = now_seen != seen[-1]
                    seen.append(now_seen)
                    if waited < timeout or (waited < hard and not argv and progressed and gpu_busy()):      # past its base time it keeps going only while a GPU works on it AND the run still produces something (files change) — a busy GPU alone (another model, a hung run) does not extend it
                        continue
                    if not argv:
                        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, creationflags=NOWIN)
                    kill_tree(p)
                    return "", "timed out after %d s%s" % (waited, " (and it produced nothing new in the last %d s)" % chunk if waited >= timeout and not argv else "")
            if p.returncode != 0:
                return out or "", "exit %s: %s" % (p.returncode, _tail((err or out or "").strip(), 3000, "run output"))
            return out or "", ""
        finally:
            if token and self.env and self.env.gateway:
                self.env.gateway.close_run(token)
            if board:
                shutil.rmtree(board, ignore_errors=True)

    @staticmethod
    def police(ws, check, write_ok=lambda rel: (True, "")):
        """after the run: put back anything the agent must not have changed (protected files, files outside its scope, binaries). -> (touched files, [violations])"""
        bad = []
        for rel, kind in sorted(ws.changes().items()):
            why = ""
            if check and is_protected(rel, check):
                why = "protected file"
            elif rel.lower().endswith(BINARY_EXT):
                why = "binary file"
            else:
                ok, w = write_ok(rel)
                if not ok:
                    why = "out of scope (%s)" % w
            if not why:
                continue
            bad.append("%s: %s (%s, reverted)" % (rel, why, kind))
            path, base = os.path.join(ws.project, rel), os.path.join(ws.base, rel)
            try:
                if kind == "added":
                    os.remove(path)
                else:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    shutil.copyfile(base, path)
            except OSError:
                pass
        return sorted(ws.changes()), bad


def make_digest(app, max_chars=6000):
    """digest(job) -> markdown: what every agent is doing right now (so it can stay out of their way and build on their work) and what the notice board already knows about this goal"""
    def digest(job):
        lines = ["# Shared notice board digest (a snapshot taken when your task started; ask the board live with mcp__agentdispatch__board_search / board_read)", "", "## Work in progress on this machine"]
        try:
            for st in ("running", "reviewing", "blocked", "queued"):
                for j in app.jobs.list(state=st, limit=8):
                    if j["id"] == job["id"]:
                        continue
                    who = ", ".join(sorted({s["agent"] for s in (app.jobs.get(j["id"]).get("steps") or []) if s.get("agent")})) or "nobody yet"
                    lines.append("- %s [%s] %s (target %s; agents: %s)%s" % (j["id"], st, j["title"][:80], j.get("target") or "-", who, ("; " + j["why"][:100]) if j.get("why") else ""))
        except Exception as e:
            lines.append("(jobs unavailable: %s)" % e.__class__.__name__)
        lines += ["", "## What the board knows about this goal"]
        try:
            res = app.board.search((job.get("title") or "") + " " + (job.get("goal") or "")[:200], limit=6).get("results") or []
            lines += ["- %s %s: %s" % (r.get("kind"), r.get("ref_id"), str(r.get("snippet") or r.get("title") or "")[:200]) for r in res] or ["- nothing similar on the board"]
        except Exception as e:
            lines.append("(board search unavailable: %s)" % e.__class__.__name__)
        return _fit("\n".join(lines), max_chars, "digest")
    return digest
