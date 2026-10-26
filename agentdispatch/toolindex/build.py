"""build the lean tool index: deterministic parse + compaction, then a LOCAL model groups the tools and writes when-to-use hints chunk by chunk; everything the model says is validated against the real catalog"""
import hashlib, json, os, re, shutil, time, urllib.request
from ..textfit import ollama_ctx

from .compact import compact_catalog, first_sentence
from ..jsonx import extract_json

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
ENGINE_ROOM = os.environ.get("ENGINEROOM_URL", "http://127.0.0.1:8898")
MODEL_PREFERENCE = ["local-qwen3-4b", "qwen3:4b", "local-qwen3.5-9b", "local-gemma3-27b", "local-gpt-oss-20b", "local-qwen3-1.7b", "qwen3:1.7b"]

DOMAINS = [                                                    # deterministic first pass: keyword -> domain (order matters)
    ("session", r"\b(session|doctor|capabilit|status|close_binary|open_binary|current_|binary_session)\b|^(open|close)_"),
    ("javascript-electron", r"javascript|electron|asar|source.?map|npm|node module|ipc|preload|bundle"),
    ("android", r"android|apk|jadx|apktool|dex"),
    ("android-device", r"\badb\b|logcat|bugreport|device"),
    ("web-browser", r"browser|website|web page|webpage|har\b|network capture|screenshot|cdp|playwright|http"),
    ("firmware", r"firmware|binwalk|unblob"),
    ("evm", r"\bevm\b|bytecode|solidity|selector"),
    ("dotnet", r"\.net|managed|cil|assembly|pe/cli"),
    ("process-capture", r"process|pty|terminal output|crash|core dump"),
    ("evidence-compare", r"evidence|ledger|reconstruction|compare|trace_application|obligation"),
    ("native-binary", r"native|ghidra|hopper|\bida\b|procedure|function|disassembl|decompil|binary|segment|xref|address|assembly|pseudocode|symbol|string"),
]


def domain_of(name, text):
    blob = (name.replace("_", " ") + " " + (text or "")).lower()
    for d, pat in DOMAINS:
        if re.search(pat, blob):
            return d
    return "other"


def log_to_engine_room(model, resp):
    try:
        body = json.dumps(dict(backend="ollama", who="agentdispatch-index", model=model, prompt_tokens=resp.get("prompt_eval_count", 0), gen_tokens=resp.get("eval_count", 0),
                               ms=(resp.get("eval_duration") or 0) / 1e6)).encode()
        urllib.request.urlopen(urllib.request.Request(ENGINE_ROOM + "/llm", body, {"Content-Type": "application/json"}), timeout=2).read()
    except Exception:
        pass


def installed_models(tries=3):
    for i in range(tries):                                         # Ollama can be slow to answer while a model is loading
        try:
            with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=15) as r:
                return [m["name"] for m in json.loads(r.read()).get("models", [])]
        except Exception:
            time.sleep(2)
    return []


def _resolve_name(name, have):
    for cand in (name, name + ":latest"):
        if cand in have:
            return cand
    return None


def pick_models(requested=None):
    have = installed_models()
    if requested:
        r = _resolve_name(requested, have)
        return [r] if r else []
    return [r for r in (_resolve_name(m, have) for m in MODEL_PREFERENCE) if r][:2]


SYSTEM = ("You write search hints for a catalog of software tools so other AI models can find the right tool from a plain-language request. "
          "Reply with ONE JSON object and nothing else. Use only the exact tool names given.")


def prompt_for(lines):
    return ('Tools (name: what it does):\n%s\n\nFor EVERY tool give: "topic" (a short kebab-case label shared by closely related tools), "keywords" (8-12 lowercase words and short phrases a person '
            'might use when they need it, including synonyms and the words the description does NOT use), "examples" (3 short example requests in plain language), "use_when" (one line). '
            'Also give "domain_summary": one line saying what this whole set of tools is for.\n'
            'Return JSON: {"domain_summary":"...","tools":[{"name":"exact_tool_name","topic":"kebab-case","keywords":["..."],"examples":["..."],"use_when":"..."}]}' % "\n".join(lines))


def ask_ollama(model, system, user, timeout=300, num_predict=3000):
    body = dict(model=model, stream=False, think=False, keep_alive="5m", format="json", options=dict(temperature=0.1, num_predict=num_predict, num_ctx=ollama_ctx(len(system) + len(user), num_predict, ceiling=65536)),
                messages=[dict(role="system", content=system), dict(role="user", content="/no_think\n" + user)])
    req = urllib.request.Request(OLLAMA + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    log_to_engine_room(model, resp)
    text = re.sub(r"(?s)<think>.*?</think>", "", (resp.get("message") or {}).get("content") or "").strip()
    return text, resp


def parse_hints(text, allowed):
    """validate the model's answer: JSON (found even after the model's reasoning), real tool names only, each tool once, bounded sizes.
    Returns ({tool: hints}, domain_summary, problems)"""
    obj = extract_json(text, "tools")
    if not isinstance(obj, dict) or not isinstance(obj.get("tools"), list):
        return {}, "", ["not a JSON object with a tools list"]
    problems, out, allowed_set = [], {}, set(allowed)
    clean = lambda v, n, m: [str(x).lower().strip()[:m] for x in (v if isinstance(v, list) else [])[:n] if str(x).strip()]
    for t in obj["tools"]:
        name = t.get("name") if isinstance(t, dict) else None
        if name not in allowed_set:
            problems.append("unknown tool %r" % str(name)[:40])
            continue
        if name in out:
            continue
        topic = re.sub(r"[^a-z0-9\-]+", "-", str(t.get("topic") or "").lower()).strip("-")[:40]
        out[name] = dict(topic=topic, keywords=clean(t.get("keywords"), 14, 40), examples=clean(t.get("examples"), 3, 120), use_when=str(t.get("use_when") or "")[:200])
    missing = [n for n in allowed if n not in out]
    return out, str(obj.get("domain_summary") or "")[:200], problems + (["missing tools: %s" % ", ".join(missing[:5])] if missing else [])


def chunks_by_domain(compact, size):
    by = {}
    for n, v in compact.items():
        by.setdefault(domain_of(n, v["card"]), []).append(n)
    out = []
    for d in sorted(by):
        names = sorted(by[d])
        for i in range(0, len(names), size):
            out.append((d, names[i:i + size]))
    return out


def default_hinters(model=None, use_llm=True):
    if not use_llm:
        return []
    from .llm import OllamaHinter
    models = pick_models(model)
    return [OllamaHinter(models)] if models else []


def build_index(tools, backend, backend_version="", model=None, use_llm=True, chunk=12, log=print, previous=None, hinters=None):
    """hinters: language models to ask for tool hints, in order (a chunk falls through to the next if one fails or answers badly); default = a local Ollama model.
    previous: an older index of the same backend: tools whose name and card are unchanged keep their model-written hints, so an upgrade only asks the model about NEW or CHANGED tools."""
    compact, stats = compact_catalog(tools)
    cat_sha = hashlib.sha256(json.dumps(sorted((t["name"], t.get("description", "")) for t in tools)).encode()).hexdigest()
    if hinters is None:
        hinters = default_hinters(model, use_llm)
    llm = dict(model=None, models={}, chunks=0, failed_chunks=0, problems=[], reused=0)
    dom_of_tool, hint_of, dom_summary = {}, {}, {}
    prev_tools = (previous or {}).get("tools", {})
    for n, v in compact.items():
        p = prev_tools.get(n)
        if p and p.get("card") == v["card"] and (p.get("hints") or {}).get("keywords") and (p.get("hints") or {}).get("by", "legacy") != "deterministic":
            hint_of[n] = dict(topic=p["hints"].get("topic", ""), keywords=p["hints"].get("keywords", []), examples=p["hints"].get("examples", []), use_when=p["hints"].get("use_when", ""),
                              by=p["hints"].get("by", (previous or {}).get("llm", {}).get("model")))
    llm["reused"] = len(hint_of)
    if use_llm and not hinters:
        log("no language model available: using deterministic hints only")
    work = []
    for dom, names_all in chunks_by_domain(compact, chunk):
        for n in names_all:
            dom_of_tool[n] = dom
        names = [n for n in names_all if n not in hint_of]
        if names:
            work.append((dom, names))

    def ask_chunk(item):
        dom, names = item
        problems_out, got, summary, who = [], {}, "", None
        lines = ["%s: %s" % (n, compact[n]["card"][:160]) for n in names]
        for h in hinters:
            try:
                text = h.ask(SYSTEM, prompt_for(lines))
            except Exception as e:
                problems_out.append("%s: %s" % (h.name, str(e)[:100]))
                continue
            cand, summ, problems = parse_hints(text, names)
            problems_out += ["%s: %s" % (h.name, p) for p in problems[:2]]
            if len(cand) >= 0.8 * len(names):
                got, summary, who = cand, summ, (getattr(text, "model", "") or getattr(h, "label", h.name))
                break
        return dom, names, got, summary, who, problems_out

    workers = max(1, int(getattr(hinters[0], "workers", 1))) if hinters else 1
    if hinters and work:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(ask_chunk, work))                  # results come back in the original order
            for k, r in enumerate(results):                           # a chunk that failed outright gets ONE more try now that the burst of errors has probably passed
                if not r[2]:
                    r2 = ask_chunk(work[k])
                    results[k] = r2[:5] + (r[5] + r2[5],)
    else:
        results = [(dom, names, {}, "", None, []) for dom, names in work]
    for dom, names, got, summary, who, problems in results:
        llm["chunks"] += 1 if hinters else 0
        llm["problems"] += problems
        if hinters and not got:
            llm["failed_chunks"] += 1
        for n in names:
            if n in got:
                hint_of[n] = dict(got[n], by=who)
                llm["models"][hint_of[n]["by"]] = llm["models"].get(hint_of[n]["by"], 0) + 1
            else:                                                       # the model skipped it (or no model): deterministic hints, never nothing
                hint_of[n] = dict(topic=dom, keywords=sorted({w for w in n.split("_") if len(w) > 3})[:8], examples=[], use_when="", by="deterministic")
        if summary:
            dom_summary.setdefault(dom, summary)
        log("%-22s %2d tools, %d hinted by %s" % (dom, len(names), len(got), who or "-"))
    if llm["models"]:
        llm["model"] = max(llm["models"], key=llm["models"].get)
    elif llm["reused"]:
        llm["model"] = (previous or {}).get("llm", {}).get("model")
    old_groups = (previous or {}).get("groups", {})
    groups = {}
    for n in compact:
        groups.setdefault(dom_of_tool.get(n, "other"), dict(summary="", when_to_use="", topics=[], tools=[]))["tools"].append(n)
    for d, g in groups.items():
        g["tools"].sort()
        by = {}
        for t in g["tools"]:
            tp = (hint_of.get(t) or {}).get("topic")
            if tp:
                by.setdefault(tp, []).append(t)
        ranked = sorted(by, key=lambda k: (-len(by[k]), k))
        g["topics"] = ranked[:8]
        g["summary"] = dom_summary.get(d) or old_groups.get(d, {}).get("summary") or "%s tools: %s" % (d.replace("-", " "), ", ".join(g["tools"][:4]))
        g["when_to_use"] = old_groups.get(d, {}).get("when_to_use", "") if not dom_summary.get(d) else ""
    tools_idx = {}
    for n, v in compact.items():
        h = hint_of.get(n) or {}
        tools_idx[n] = dict(card=v["card"], blurb=v.get("blurb", ""), title=v["title"], group=dom_of_tool.get(n, "other"), risk=v["risk"], cut_level=v["cut_level"], schema_sha=v["schema_sha"],
                            hints=dict(keywords=h.get("keywords", []), examples=h.get("examples", []), use_when=h.get("use_when", ""), topic=h.get("topic", ""), by=h.get("by", "")))
    index = dict(format=2, built=time.strftime("%Y-%m-%d %H:%M:%S"), source=dict(backend=backend, backend_version=backend_version, tool_count=len(tools), catalog_sha256=cat_sha),
                 llm=llm, stats=stats, groups=groups, tools=tools_idx)
    return index, {n: v["schema"] for n, v in compact.items()}


def save_index(data_dir, backend, index, schemas, full_tools):
    """write the three files into a new folder and swap it in as a unit, so a reader never sees a new index.json next to an old schemas.json"""
    base = os.path.join(data_dir, "index")
    final, new, old = (os.path.join(base, backend + s) for s in ("", ".new", ".old"))
    for d in (new, old):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(new)
    for name, obj in (("index.json", index), ("schemas.json", schemas), ("full_schemas.json", {t["name"]: t.get("inputSchema") for t in full_tools})):
        with open(os.path.join(new, name), "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    if os.path.isdir(final):
        try:                                                              # keep what is being replaced, named by who wrote its hints, so two indexes can be compared or restored
            prev = json.load(open(os.path.join(final, "index.json"), encoding="utf-8"))
            tag = re.sub(r"[^A-Za-z0-9._+\-]", "_", str((prev.get("llm") or {}).get("model") or "deterministic"))[:40]
            hist = os.path.join(base, "history", "%s_%s_%s" % (backend, prev.get("built", "x").replace(":", "").replace(" ", "_"), tag))
            os.makedirs(os.path.dirname(hist), exist_ok=True)
            if not os.path.isdir(hist):
                shutil.copytree(final, hist)
        except Exception:
            pass
        os.replace(final, old)
    os.replace(new, final)
    shutil.rmtree(old, ignore_errors=True)
    return final
