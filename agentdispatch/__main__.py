import argparse, json, os, sys, time
from .paths import OLLAMA_URL as _OLLAMA

from .app import App, DEFAULT_DATA


def _agent_selftest(app, env, wait=False):
    """the local agent's start-up self-test (always runs when the agency daemon starts and ollama-agent is enabled; data/routing.json "agent_selftest_create": true lets it create missing variants): a model that is missing or cannot get a valid pinned-window name is marked invalid for
    ollama-agent, so it is never handed out ("unrecognized_model"); if none passes the agent is cooled down. Runs in the background: Ollama may be slow to answer."""
    import threading
    agent = app.registry.agents.get("ollama-agent")
    if not agent or not agent.get("enabled", True):
        return None

    def go():
        try:
            from .agency.routing import key_of
            models = [key_of(r) for r in app.availability.router.ladder(agent)]
            res = env.selftest(models, create=bool((app.availability.router.cfg or {}).get("agent_selftest_create", False)))
            bad = [r for r in res if not r["ok"]]
            for r in bad:
                try:
                    app.availability.book.invalid(agent, r["model"], "self-test: " + r["why"])
                except Exception:
                    pass
            if res and len(bad) == len(res):
                app.jobs.cooldown_set("ollama-agent", "self-test: no model passed (%s)" % "; ".join(r["why"] for r in bad)[:150], base_min=60)
            print("ollama-agent self-test: %d of %d model(s) usable%s" % (len(res) - len(bad), len(res), ("; " + "; ".join("%s: %s" % (r["model"], r["why"]) for r in bad))[:400] if bad else ""))
            return res
        except Exception as e:
            print("ollama-agent self-test could not run: %s: %s" % (e.__class__.__name__, str(e)[:200]))
    if wait:
        return go()
    threading.Thread(target=go, name="agent-selftest", daemon=True).start()
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentdispatch", description="AgentDispatch: notice board, job queue and review gate for AI agents, served over MCP (stdio or HTTP)")
    ap.add_argument("--data", default=DEFAULT_DATA)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("stdio", help="MCP over stdin/stdout (what Claude Code / Codex / Grok launch)")
    s.add_argument("--profile", default="full", choices=["lite", "full"])
    s.add_argument("--actor", default=None, help="fix the author name used for posts (default: the client's own name)")
    s = sp.add_parser("serve", help="HTTP: /mcp, the bulletin page and the JSON API (127.0.0.1 only)")
    s.add_argument("--port", type=int, default=8870)
    s.add_argument("--host", default=None, help="address to listen on (default: $AGENTDISPATCH_HOST or 127.0.0.1; the Docker image uses 0.0.0.0)")
    s = sp.add_parser("token", help="create a bearer token for an HTTP client")
    s.add_argument("name")
    s.add_argument("--owner", action="store_true", help="owner role: may accept, reject and cancel jobs and allow project folders (give this only to people)")
    s = sp.add_parser("agency", help="the agency (Stage 0: plans and assigns, runs no real agent): status | submit | tick --dry | lock | verify")
    s.add_argument("action", choices=["status", "submit", "tick", "lock", "verify", "list", "run", "diff", "show", "unblock", "agents", "agent-add", "capabilities", "conductor"])
    s.add_argument("job", nargs="?", help="(diff, show, unblock) the job id; (agent-add) the new agent's id")
    s.add_argument("--kind", default="cli", choices=["cli", "app", "local"], help="(agent-add)")
    s.add_argument("--name", default=None, help="(agent-add) display name")
    s.add_argument("--strengths", default="write", help="(agent-add) comma list from: code, review, long-context, vision, fast, plan, research, write, video, speech, audio, image")
    s.add_argument("--cost", default="plan", choices=["free", "local", "cheap", "plan", "premium"], help="(agent-add)")
    s.add_argument("--region", default=None, help="(agent-add) frontal, parietal, temporal, occipital, cerebellum, brainstem, broca, auditory, limbic (default: from the strengths)")
    s.add_argument("--color", default=None, help="(agent-add) #rrggbb")
    s.add_argument("--argv", default=None, help="(agent-add) the command as JSON list, e.g. '[\"mycli\", \"-p\", \"{prompt}\"]'; omit for a pull-only agent that takes work from the board")
    s.add_argument("--once", action="store_true", help="(run) tick until nothing is left to do, then stop (default: keep running)")
    s.add_argument("--max-ticks", type=int, default=60, help="(run --once) safety cap")
    s.add_argument("--dry", action="store_true", help="(tick) only say what would happen")
    s.add_argument("--json", help="(submit) the job as JSON: title, goal, kind, target, acceptance, steps")
    s = sp.add_parser("models", help="the model catalog: find the AI models on this computer and choose which to use: scan | list | pick | select | pin | roots")
    s.add_argument("action", choices=["scan", "list", "pick", "select", "unselect", "favorite", "hide", "rename", "add-root", "remove-root", "status"])
    s.add_argument("target", nargs="?", help="(pick, select) the function, e.g. speech-duplex; (add-root) a folder; (favorite, hide, rename) a model id or name")
    s.add_argument("model", nargs="?", help="(select) the model id or part of its name; (rename) the new name")
    s.add_argument("--seconds", type=float, default=90, help="(scan) time budget for the deep walk of every drive")
    s.add_argument("--quick", action="store_true", help="(scan) known places only, no walk of the drives")
    s.add_argument("--function", default=None, help="(list) only this function")
    s.add_argument("-q", default=None, help="(list) search text")
    s = sp.add_parser("tools", help="what this computer can run for the models: list | scan | run | install | approve | deny | need | policy")
    s.add_argument("action", choices=["list", "scan", "run", "install", "approve", "deny", "need", "policy", "runs"])
    s.add_argument("target", nargs="?", help="(run, install) the tool id; (approve, deny) the request id; (need) the new tool's name")
    s.add_argument("--code", default=None, help="(run) Python or Lean source")
    s.add_argument("--file", default=None, help="(run) a file")
    s.add_argument("--args", nargs="*", default=[], help="(run) arguments")
    s.add_argument("--cwd", default=None)
    s.add_argument("--description", default="", help="(need) what the tool must do")
    s.add_argument("--trust", action="append", default=[], help="(policy) a folder whose code may run; repeatable")
    s.add_argument("--auto-pip", choices=["on", "off"], default=None, help="(policy) let the program install allow-listed Python packages by itself")
    s = sp.add_parser("campaign", help="the campaign gates: status | import | load | next | log | decide | transition | template")
    s.add_argument("action", choices=["status", "import", "load", "next", "log", "decide", "transition", "template", "templates"])
    s.add_argument("target", nargs="?", help="(import) a file; (load) a JSON file; (next, log) a campaign id; (decide) a proposal id; (transition) a gate id; (template) a template id")
    s.add_argument("--campaign", default="C1")
    s.add_argument("--approve", action="store_true", help="(decide)")
    s.add_argument("--reject", action="store_true", help="(decide)")
    s.add_argument("--to", default=None, help="(transition) the new status")
    s.add_argument("--gate", default="", help="(template)")
    s.add_argument("--cutoff", default="", help="(template) evidence cutoff commit")
    s = sp.add_parser("playbooks", help="the library of finished workflows: list | search | show | failures | suggest | export | import")
    s.add_argument("action", choices=["list", "search", "show", "failures", "suggest", "export", "import"])
    s.add_argument("target", nargs="?", help="(search, suggest) text; (show) an id; (import) a file")
    s.add_argument("--category", default="")
    s.add_argument("--outcome", default="")
    s = sp.add_parser("autonomy", help="autonomous mode: on | off | status | trial NAME (queue the overnight trial; the broker starts it) | usage")
    s.add_argument("action", choices=["on", "off", "status", "trial", "usage"])
    s.add_argument("name", nargs="?", default="csv_report_service")
    s.add_argument("--root", default=None, help="(on) the autonomous projects folder")
    s = sp.add_parser("nightshift", help="a local model works the issues marked for it (00:00-06:00; --force to run now); nothing is applied to real code")
    s.add_argument("action", nargs="?", default="run", choices=["run", "status", "setup"], help="run (default) | status: what would happen | setup: add --root PATH / --trust NAME")
    s.add_argument("--root", action="append", default=[], help="(setup) a project folder checks may copy from; repeatable")
    s.add_argument("--trust", action="append", default=[], help="(setup) an author whose checks are trusted even without the container; repeatable")
    s.add_argument("--isolation", default=None, choices=["auto", "docker", "none"], help="(setup) where checks run")
    s.add_argument("--force", action="store_true", help="ignore the night window")
    s.add_argument("--daemon", action="store_true", help="keep running; one pass per night window")
    s.add_argument("--max-issues", type=int, default=3)
    s = sp.add_parser("orchestrate", help="triage the open issues (writes notes and suggestions to the board only)")
    s.add_argument("--llm", default="gemini", choices=["gemini", "openrouter", "ollama"], help="first choice; the others are fallbacks")
    s.add_argument("--gemini-model", default="gemini-3.5-flash-lite")
    s.add_argument("--openrouter-model", default="openrouter/free", help="OpenRouter free model id (needs OPENROUTER_API_KEY)")
    s.add_argument("--ollama-model", default="local-qwen3-4b")
    s.add_argument("--dry-run", action="store_true", help="only list what is waiting for triage")
    a = ap.parse_args(argv)
    if a.cmd == "stdio":
        app = App(a.data)
        app.mcp.serve_stdio(a.profile, a.actor)
        return 0
    if a.cmd == "serve":
        from .web import serve
        serve(App(a.data), a.port, a.host)
        return 0
    if a.cmd == "token":
        from .web import new_token
        print(new_token(a.data, a.name, "owner" if a.owner else "agent"))
        return 0
    if a.cmd == "agency":
        from .agency import gate as AG
        from .agency.dispatcher import Dispatcher
        from .agency.adapters import Adapter
        from .web import agency_status, slim_job
        app = App(a.data)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if a.action == "status":
            print(json.dumps(agency_status(app), indent=1))
        elif a.action == "list":
            print(json.dumps([slim_job(j) for j in app.jobs.list(limit=50)], indent=1))
        elif a.action == "submit":
            d = json.loads(a.json or "{}")
            j = app.jobs.create(d.get("owner", "owner"), d["title"], d["goal"], d.get("kind", "code"), d.get("target", ""), d.get("acceptance"), d.get("budget"), d.get("priority", 1), d.get("meta"))
            if d.get("steps"):
                j = app.jobs.add_steps(j["id"], d["steps"], "owner")
            print(json.dumps(slim_job(j), indent=1))
        elif a.action == "agents":
            from .agency import brain as BR
            for c in BR.agent_cards(app):
                print("%-14s %-10s %-9s %-9s %s" % (c["id"], c["region"], c["state"], c["color"], c["why"]))
        elif a.action == "agent-add":
            import copy
            from .agency.registry import DEFAULTS, validate, Registry
            if not a.job:
                print("give the new agent's id: agency agent-add my-agent --strengths video --kind app"); return 2
            path = os.path.join(app.data, "agents.json")
            try:
                cur = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError):
                cur = copy.deepcopy(DEFAULTS)
            entry = dict(kind=a.kind, name=a.name or a.job, strengths=[x.strip() for x in a.strengths.split(",") if x.strip()], cost=a.cost, concurrency=1, daily_cap=10, sandbox="copy",
                         enabled=True, run=(dict(argv=json.loads(a.argv), stdin_prompt=True, timeout_s=1800) if a.argv else dict(via="pull")))
            if not a.argv:
                entry.update(enabled=False, daily_cap=0, sandbox="none")
            if a.region or a.color:
                entry["brain"] = {k: v for k, v in (("region", a.region), ("color", a.color)) if v}
            cur[a.job] = entry
            problems = validate(cur)
            if problems:
                print("refused: " + "; ".join(problems[:4])); return 1
            json.dump(cur, open(path, "w", encoding="utf-8", newline="\n"), indent=1)
            print("added %s -> %s (region %s)" % (a.job, path, Registry(app.data).brain(a.job)["region"]))
        elif a.action == "unblock":
            print(json.dumps(slim_job(app.jobs.unblock(a.job, "owner")), indent=1))
        elif a.action in ("diff", "show"):
            from .agency.workspace import Workspace
            from .agency import policy as POL
            j = app.jobs.get(a.job)
            if a.action == "show":
                print(json.dumps(j, indent=1, default=str))
            else:
                print(Workspace(POL.load(app.data)["work_dir"], a.job, j["created"]).diff(100000) or "(no changes)")
        elif a.action in ("tick", "run"):
            if a.action == "tick" and a.dry:
                print(json.dumps(Dispatcher(app.board, app.jobs, app.registry, app.availability, Adapter(), app.data).tick(dry=True), indent=1))
                return 0
            from .agency import policy as POL
            from .agency.realadapters import RealAdapter
            from .agency.runner import CheckRunner
            from .agency.workspace import Workspace
            from .agency.apply import make_apply
            cfg = POL.load(app.data)
            runner = CheckRunner(app.data)

            def chat(prompt, model=None):
                mt_cfg = app.availability.router.cfg.get("max_tokens") or {}
                is_local = not model or "/" not in model and not str(model).startswith(("gemini", "openrouter"))
                req = dict(model=model or "auto-local", messages=[dict(role="user", content=prompt)], response_format=dict(type="json_object"))
                limit = int(mt_cfg.get("local", 0) if is_local else mt_cfg.get("default", 32768))
                if limit > 0:                                                                                     # 0 = no cap: a local model finishes the whole file, however long it takes
                    req["max_tokens"] = limit
                used = {"model": None}

                def send(p):
                    r = app.router.chat(dict(req, messages=[dict(role="user", content=p)]), "agency")      # a continuation carries the whole partial reply: any model can finish it
                    tried = (r.get("agentdispatch") or {}).get("route") or []
                    used["model"] = next((t.get("model") for t in reversed(tried) if t.get("ok")), None) or used["model"]   # which model the router actually used
                    ch = r["choices"][0]
                    return ch["message"].get("content") or "", ch.get("finish_reason")
                from .agency.llmcall import complete
                text, _n = complete(send, prompt)                                                                # a reply cut off by the output limit inside its JSON is continued, not passed on half
                if not text.strip():                                                                              # a reasoning model that spent the whole budget thinking: say so (the caller re-asks briefly)
                    return "", used["model"]
                return text, used["model"]

            def checker(job):
                return runner.run_job(Workspace(cfg["work_dir"], job["id"], job.get("created")).project, job.get("acceptance"), bool(cfg.get("allow_host_checks")))
            from .agency.agentic import make_digest
            ad = RealAdapter(cfg["work_dir"], chat, toolbox=app.localtools, governor=app.campaign.governor, book=app.availability.book)
            ad.digest = make_digest(app)
            disp = Dispatcher(app.board, app.jobs, app.registry, app.availability, ad, app.data, cfg=cfg, checker=checker,
                              say=print, hooks=app.campaign.governor, apply_fn=make_apply(app.data, cfg["work_dir"], runner.run))
            if a.action == "tick":
                print(json.dumps(disp.tick(), indent=1, default=str))
                return 0
            problems = disp.startup_check()
            if problems:
                print("the agency will not start: " + "; ".join(problems)); return 1
            app.jobs.recover()
            if a.once:
                for i in range(a.max_ticks):
                    t = disp.tick()
                    print(json.dumps(t, default=str))
                    if t["action"] in ("idle", "wait"):
                        break
                return 0
            from .agency.conductor import Conductor
            from .agency import policy as _POL
            from .agency.agentic import AgentEnv, AgenticRunner
            from .agency.gateway import Gateway
            gw = Gateway(app, app.capabilities, port=int(cfg.get("gateway_port", 8871))).start()                  # the one door a containerised agent has (firewalled to this address)
            ad.agentic = AgenticRunner(env=AgentEnv(app, gw, app.capabilities, cfg=lambda: app.availability.router.cfg.get("agent_ctx"), watch=app.availability.vram), gateway_port=gw.port)
            _agent_selftest(app, ad.agentic.env)

            def conductor_ask(prompt):
                """hot local Ollama by default (local-qwen3-4b): routes the machine without burning Claude session limits. Set conductor.json backend=agent to use a registry CLI instead."""
                import json as _json, urllib.request as _ur
                cc = Conductor(app, None).cfg()
                backend = (cc.get("backend") or "ollama").lower()
                if backend == "ollama":
                    from .textfit import fit as _fit, ollama_ctx, max_prompt_chars, CTX_MAX
                    model = cc.get("model") or "local-qwen3-4b:latest"
                    sys_text = "You are the AgentDispatch conductor. Reply with ONLY a JSON object that has a swaps array (and optional notes). No markdown."
                    reply = int(cc.get("num_predict") or 2048)
                    prompt = _fit(prompt, max_prompt_chars(int(cc.get("num_ctx_max") or CTX_MAX), reply), "conductor prompt")      # never past the window: Ollama would drop the FRONT (the instructions)
                    body = _json.dumps(dict(
                        model=model,
                        stream=False,
                        think=False,
                        format="json",
                        keep_alive=cc.get("keep_alive", "24h"),
                        options=dict(num_ctx=max(int(cc.get("num_ctx") or 8192), ollama_ctx(len(prompt) + len(sys_text), reply, ceiling=int(cc.get("num_ctx_max") or CTX_MAX))),
                                     num_predict=reply, temperature=0.2),
                        messages=[
                            dict(role="system", content=sys_text),
                            dict(role="user", content=prompt),
                        ],
                    )).encode("utf-8")
                    req = _ur.Request(cc.get("ollama", _OLLAMA).rstrip("/") + "/api/chat", data=body,
                                      headers={"Content-Type": "application/json"}, method="POST")
                    with _ur.urlopen(req, timeout=180) as r:
                        data = _json.loads(r.read().decode("utf-8", "replace"))
                    text = ((data.get("message") or {}).get("content") or data.get("response") or "").strip()
                    if not text:
                        raise RuntimeError("local conductor model %s returned empty" % model)
                    return text
                ag = app.registry.agents.get(cc.get("agent") or "openrouter")
                if not ag:
                    raise RuntimeError("conductor agent %r is not in the registry" % cc.get("agent"))
                ad.hint = dict(difficulty=cc.get("difficulty", 0.4), kind="plan", attempts=0)
                text, err = ad._ask(ag, prompt, None)
                if err:
                    raise RuntimeError(err)
                return text
            cond = Conductor(app, conductor_ask)
            cond.start()                                                                                 # the live conductor: swaps models as the work goes on (conductor_off in the data folder stops it)
            from .agency.watchdog import Watchdog
            dog = Watchdog(app)
            workers = max(1, int(cfg.get("max_parallel", 1)))
            stop = __import__("threading").Event()

            def worker(i):
                while not stop.is_set():
                    try:
                        disp.cfg = _POL.load(app.data)
                        disp.tick()
                        if i == 0:                                                                       # one worker owns the stuck-job watchdog
                            try:
                                dog.tick()
                            except Exception as we:
                                print("watchdog error: %s: %s" % (we.__class__.__name__, we), flush=True)
                    except Exception as e:
                        print("agency worker %d error: %s: %s" % (i, e.__class__.__name__, e), flush=True)
                    time.sleep(disp.cfg["tick_s"] + i * 0.7)                                             # a local model and the cloud agents work at the same time: one worker each
            for i in range(1, workers):
                __import__("threading").Thread(target=worker, args=(i,), daemon=True, name="agency-worker-%d" % i).start()
            worker(0)
        elif a.action == "conductor":                                                # the conductor's recent decisions (job = off | on to stop / resume it)
            flag = os.path.join(app.data, "conductor_off")
            if a.job == "off":
                open(flag, "w").close()
            elif a.job == "on" and os.path.exists(flag):
                os.remove(flag)
            lines = open(os.path.join(app.data, "conductor_log.jsonl"), encoding="utf-8").read().splitlines()[-8:] if os.path.exists(os.path.join(app.data, "conductor_log.jsonl")) else []
            print(json.dumps(dict(running=not os.path.exists(flag), recent=[json.loads(l) for l in lines]), indent=1, default=str))
        elif a.action == "capabilities":                                             # what containerised agents may use: list | approve C-n | deny C-n  (job = the sub-command)
            sub = a.job or "list"
            cid = (a.name or "")
            if sub in ("approve", "deny"):
                print(json.dumps(app.capabilities.decide(cid, sub == "approve", "owner"), indent=1, default=str))
            else:
                print(json.dumps(app.capabilities.listing(), indent=1, default=str))
        elif a.action == "lock":
            print(json.dumps(AG.lock(root, app.data), indent=1))
        elif a.action == "verify":
            bad = AG.verify_lock(root, app.data)
            print("gate files match the lock" if not bad else chr(10).join(bad))
            return 1 if bad else 0
        return 0
    if a.cmd == "autonomy":
        from .intake import autonomy as AU, Intake
        app = App(a.data)
        if a.action == "on":
            print(json.dumps(AU.enable(app, a.root), indent=1))
        elif a.action == "off":
            print(json.dumps(AU.disable(app), indent=1))
        elif a.action == "usage":
            print(json.dumps(app.usage.summary(), indent=1, default=str))
        elif a.action == "trial":
            text = AU.trial_text(a.name)
            if not text or not app.usage.autonomy.load()["enabled"]:
                print("turn autonomous mode on first (autonomy on), and name an existing trial in docs/trials"); return 1
            it = Intake(app)
            s = it.new(True)
            it.say(s["id"], "user", text)
            it.update(s["id"], lambda se: se.update(work=dict(state="queued", action="auto")))
            print("queued %s: the broker starts it within a minute" % s["id"])
        else:
            print(json.dumps(dict(config=app.usage.autonomy.load(), strong_today=app.usage.summary()["strong_calls_today"]), indent=1))
        return 0
    if a.cmd in ("tools", "campaign", "playbooks"):
        app = App(a.data)
        api = app.api
        show = lambda r: print(json.dumps(r, indent=1, default=str, ensure_ascii=False))
        if a.cmd == "tools":
            if a.action == "list":
                for t in api.tools(0)["tools"]:
                    print("%-14s %-8s %-9s %s" % (t["id"], t["kind"], "ready" if t["available"] else "missing", (t["version"] or t["note"])[:70]))
            elif a.action == "scan":
                show({k: v for k, v in api.scan().items() if k in ("scanned", "pythons")})
            elif a.action == "run":
                r = api.run(dict(tool=a.target, code=a.code, file=a.file, args=a.args, cwd=a.cwd), "owner-cli")
                print(r.get("stdout", "") + (("\n--stderr--\n" + r["stderr"]) if r.get("stderr") else "") + ("\n".join(r.get("problems") or [])))
                return 0 if r.get("ok") else 1
            elif a.action == "install":
                show(api.install(dict(tool=a.target), "owner-cli"))
            elif a.action in ("approve", "deny"):
                show(api.decide_install(a.target, a.action == "approve", "owner"))
            elif a.action == "need":
                show(api.need(dict(name=a.target, description=a.description), "owner-cli"))
            elif a.action == "runs":
                for r in app.localtools.history(30):
                    print("%s %-10s %-6s %5.1fs %s" % (time.strftime("%m-%d %H:%M", time.localtime(r["t"])), r["tool"], "ok" if r.get("ok") else "FAIL", r.get("secs") or 0, r.get("caller") or ""))
            elif a.action == "policy":
                upd = {}
                if a.trust:
                    upd["trusted_roots"] = list(app.localtools.cfg.get("trusted_roots") or []) + a.trust
                if a.auto_pip:
                    upd["auto_install"] = ["pip"] if a.auto_pip == "on" else []
                show(api.tool_config(upd)["config"] if upd else api.tools(0)["config"])
        elif a.cmd == "campaign":
            if a.action == "status":
                for c in api.campaign()["campaigns"]:
                    print("%s: %s (next: %s)" % (c["id"], c["counts"], c["next"]))
                    for g in c["gates"]:
                        print("  %-9s %-28s %s%s" % (g["derived"], g["id"][:28], g["status"], ("  waiting for " + ", ".join(g["waiting_for"])) if g["waiting_for"] else ""))
            elif a.action == "import":
                show(api.campaign_import(a.campaign, open(a.target, encoding="utf-8").read(), "owner-cli")["created"])
            elif a.action == "load":
                api.campaign_load(json.load(open(a.target, encoding="utf-8")), "owner-cli")
                print("loaded")
            elif a.action == "next":
                show(api.campaign_next(a.target or a.campaign))
            elif a.action == "log":
                for r in api.campaign_log(a.target)["log"]:
                    print(time.strftime("%m-%d %H:%M", time.localtime(r["t"])), r["action"], r.get("gate", ""), r.get("to", ""), r.get("actor", ""))
            elif a.action == "decide":
                show(api.decide(a.target, a.approve and not a.reject, "owner"))
            elif a.action == "transition":
                show(api.transition(dict(campaign=a.campaign, gate=a.target, to=a.to, why="set at the command line"), "owner"))
            elif a.action == "templates":
                for t in api.campaign()["templates"]:
                    print("%-24s %s" % (t["id"], t["summary"]))
            elif a.action == "template":
                show(api.job_from_template(dict(template=a.target, fields=dict(gate=a.gate, campaign=a.campaign, cutoff=a.cutoff)), "owner"))
        else:
            if a.action == "list":
                for p in api.playbooks("", a.category, a.outcome)["playbooks"]:
                    print("%-10s %-8s %-28s %s" % (p["job"], p["outcome"], p["category"], p["title"][:50]))
            elif a.action == "search":
                for p in api.playbooks(a.target or "", a.category, a.outcome)["playbooks"]:
                    print("%.2f %-10s %-8s %s" % (p["score"], p["job"], p["outcome"], p["title"][:60]))
            elif a.action == "show":
                show(api.playbook(a.target))
            elif a.action == "failures":
                for f in api.failures(a.category)["failures"]:
                    print("%3dx %-14s %s" % (f["count"], f["kind"], f["reason"][:90]))
            elif a.action == "suggest":
                show(api.suggest(a.target or ""))
            elif a.action == "export":
                show(api.playbook_export()["bundle"])
            elif a.action == "import":
                show(api.playbook_import(json.load(open(a.target, encoding="utf-8"))))
        return 0
    if a.cmd == "models":
        from .models.catalog import Catalog, FUNCTIONS, FUNC_LABEL
        cat = Catalog(a.data)

        def find(key):
            rows = cat.list(components=True, hidden=True, limit=5000)
            hit = [m for m in rows if key in (m["id"], m["name"], m.get("label"))] or [m for m in rows if key and key.lower() in ((m.get("label") or m["name"]) + " " + m["path"]).lower()]
            if len(hit) != 1:
                print("%s model(s) match %r%s" % (len(hit) or "no", key, ": " + ", ".join("%s (%s)" % (m["name"], m["id"]) for m in hit[:6]) if hit else ""))
                return None
            return hit[0]
        if a.action == "scan":
            stats = cat.scan(a.seconds, deep=not a.quick, progress=lambda m: print("  " + m, flush=True))
            print("found %d models in %.0f s (%d folders looked at%s)" % (stats["found"], stats["seconds"], stats["dirs"], ", time budget reached - run again with a longer --seconds" if stats["timed_out"] else ""))
            print("by function:", cat.counts())
        elif a.action == "list":
            for m in cat.list(function=a.function, q=a.q, limit=400):
                print("%-9s %-14s %7.1f GB %5s  %-45s %s%s" % (m["id"], m["function"] or m["modality"], m["size_gb"], ("%gB" % m["params_b"]) if m["params_b"] else "", (m.get("label") or m["name"])[:45],
                                                          ",".join(m["runners"]), "  *" + ",".join(m["selected_for"]) if m["selected_for"] else ""))
        elif a.action == "status":
            print(json.dumps(dict(total=len(cat.d["models"]), counts=cat.counts(), selection={f: s["model_id"] + " by " + s["by"] for f, s in cat.d["selection"].items()}, roots=cat.d["roots"], scanned=cat.d.get("scanned")), indent=1))
        elif a.action == "pick":
            r = cat.pick(a.target)
            print(json.dumps(dict(model=(r["model"] or {}).get("name"), by=r["by"], reasons=r["reasons"], alternatives=r["alternatives"]), indent=1))
        elif a.action in ("select", "unselect"):
            if a.target not in FUNCTIONS:
                print("function must be one of: " + ", ".join(FUNCTIONS)); return 2
            m = find(a.model) if a.action == "select" else None
            if a.action == "select" and not m:
                return 1
            print(json.dumps(cat.select(a.target, m["id"] if m else None, by="user"), indent=1))
        elif a.action in ("favorite", "hide", "rename"):
            m = find(a.target)
            if not m:
                return 1
            kv = dict(favorite=not m["favorite"]) if a.action == "favorite" else dict(hidden=not m["hidden"]) if a.action == "hide" else dict(label=a.model or "")
            print(cat.override(m["id"], **kv)["label"] or m["name"], kv)
        elif a.action in ("add-root", "remove-root"):
            print((cat.add_root if a.action == "add-root" else cat.remove_root)(a.target))
        return 0
    if a.cmd == "nightshift":
        from .nightshift import NightShift, daemon, load_config, write_config, status
        from .orchestrator import ollama_llm
        app = App(a.data)
        if a.action == "setup":
            print(json.dumps(write_config(app.data, allowed_roots=[r.replace("\\", "/") for r in a.root], trusted_authors=a.trust, isolation=a.isolation), indent=1))
            return 0
        if a.action == "status":
            print(json.dumps(status(app.board, app.data), indent=1))
            return 0
        cfg = load_config(app.data)
        shift = NightShift(app.board, ollama_llm(cfg["model"]), cfg)
        if a.daemon:
            daemon(shift)
            return 0
        print(json.dumps(shift.run_once(a.force, a.max_issues), indent=1))
        return 0
    if a.cmd == "orchestrate":
        from .orchestrator import Orchestrator, gemini_llm, ollama_llm, openrouter_llm
        app = App(a.data)
        g, o = gemini_llm(a.gemini_model), ollama_llm(a.ollama_model)
        or_llm = openrouter_llm(a.openrouter_model, data_dir=app.data)
        primary = {"gemini": g, "openrouter": or_llm, "ollama": o}[a.llm]
        fallback = o if a.llm != "ollama" else g
        orch = Orchestrator(app.board, primary, fallback)
        if a.dry_run:
            print("waiting for triage:", orch.pending() or "nothing")
            return 0
        r = orch.sweep()
        print("triaged %d, skipped %d" % (r["triaged"], r["skipped"]))
        for pid, rec in r["results"].items():
            print("  %s -> %s, difficulty %d, tier %s: %s" % (pid, rec["category"], rec["difficulty"], rec["tier"], rec["next_step"]))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
