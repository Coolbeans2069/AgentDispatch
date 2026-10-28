"""the catalog: what a scan found, what the user decided about it (names, hide, favourite, which model to use for what) and how the AI chooses when the user has not decided.
Stored in <data>/models.json (written atomically). A scan never loses the user's decisions; a model that is gone is marked missing, not forgotten."""
import json, os, threading, time

from . import scan as S

FUNCTIONS = ("text", "code", "reasoning", "vision", "image", "image-edit", "video", "speech-stt", "speech-tts", "speech-duplex", "music", "embedding", "objects", "depth", "segmentation")
FUNC_LABEL = {"text": "chat / writing", "code": "coding", "reasoning": "reasoning", "vision": "see pictures (vision-language)", "image": "make images", "image-edit": "edit images", "video": "make video",
              "speech-stt": "speech to text", "speech-tts": "text to speech", "speech-duplex": "voice conversation (speech to speech)", "music": "make music / sound", "embedding": "embeddings / search",
              "objects": "recognise objects and patterns", "depth": "estimate depth", "segmentation": "cut out / segment"}


def function_of(m):
    """the job a model is for: the key the user (or the AI) selects it under"""
    mod, sub = m.get("modality"), m.get("subtype")
    if m.get("component"):
        return None
    return {("text", "code"): "code", ("text", "reasoning"): "reasoning", ("text", "chat"): "text", ("vision", "vlm"): "vision", ("image", "generation"): "image", ("image", "edit"): "image-edit",
            ("video", "generation"): "video", ("speech", "stt"): "speech-stt", ("speech", "tts"): "speech-tts", ("speech", "duplex"): "speech-duplex", ("audio", "music"): "music",
            ("embedding", "text"): "embedding", ("recognition", "objects"): "objects", ("recognition", "depth"): "depth",
            ("recognition", "segmentation"): "segmentation"}.get((mod, sub))


class Catalog:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "models.json")
        self.lock = threading.RLock()
        self.status = dict(running=False, message="", started=0, finished=0, found=0)
        self.d = dict(version=1, roots=[], scanned=0, stats={}, models={}, overrides={}, selection={})
        try:
            self.d.update(json.load(open(self.path, encoding="utf-8")))
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------ storage
    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.d, f, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ scanning
    def merge(self, found, stats):
        """fold a scan into the catalog: new models are added, known ones refreshed, ones not seen this time are marked missing (a deep scan that timed out does not mark anything missing)"""
        with self.lock:
            now = time.time()
            seen = set()
            for m in found:
                old = self.d["models"].get(m["id"], {})
                m["first_seen"] = old.get("first_seen", now)
                m["last_seen"] = now
                m["present"] = True
                self.d["models"][m["id"]] = m
                seen.add(m["id"])
            ollama_listed = any(x["source"] == "ollama" for x in found)
            for mid, m in self.d["models"].items():
                if mid in seen:
                    continue
                if m.get("source") == "ollama":
                    if ollama_listed:                                              # Ollama answered and no longer lists it
                        m["present"] = False
                else:
                    m["present"] = os.path.exists(m["path"])                     # a file or folder still there but outside this scan's reach stays present
            self.d["scanned"], self.d["stats"] = now, stats
            self._save()

    def scan(self, budget_s=60.0, deep=True, progress=None):
        found, stats = S.scan(self.d["roots"], budget_s, deep, progress)
        self.merge(found, stats)
        return stats

    def scan_background(self, budget_s=60.0, deep=True):
        """start a scan in a thread; progress is in self.status. -> False when one is already running"""
        with self.lock:
            if self.status["running"]:
                return False
            self.status = dict(running=True, message="starting", started=time.time(), finished=0, found=0)

        def run():
            try:
                stats = self.scan(budget_s, deep, lambda m: self.status.update(message=m))
                self.status.update(running=False, message="done" + (" (time budget reached: run a longer deep scan for the rest)" if stats.get("timed_out") else ""), finished=time.time(), found=stats["found"])
            except Exception as e:
                self.status.update(running=False, message="failed: %s: %s" % (e.__class__.__name__, e), finished=time.time())
        threading.Thread(target=run, daemon=True).start()
        return True

    # ------------------------------------------------------------ roots (folders the user adds)
    def add_root(self, path):
        p = os.path.normpath(path)
        if not os.path.isdir(p):
            raise ValueError("not a folder: %s" % path)
        with self.lock:
            if p not in self.d["roots"]:
                self.d["roots"].append(p)
                self._save()
        return self.d["roots"]

    def remove_root(self, path):
        with self.lock:
            self.d["roots"] = [r for r in self.d["roots"] if os.path.normpath(r) != os.path.normpath(path)]
            self._save()
        return self.d["roots"]

    # ------------------------------------------------------------ listing
    def view(self, m):
        o = self.d["overrides"].get(m["id"], {})
        v = dict(m)
        v.update({k: o[k] for k in ("label", "modality", "subtype", "note") if k in o})
        v["hidden"], v["favorite"] = bool(o.get("hidden")), bool(o.get("favorite"))
        v["function"] = function_of(v)
        v["selected_for"] = [f for f, s in self.d["selection"].items() if s.get("model_id") == m["id"]]
        return v

    def list(self, function=None, modality=None, q=None, runner=None, components=False, hidden=False, present_only=True, limit=500):
        out = []
        ql = (q or "").lower()
        for m in self.d["models"].values():
            v = self.view(m)
            if present_only and not v.get("present", True):
                continue
            if v["hidden"] and not hidden:
                continue
            if v.get("component") and not components:
                continue
            if function and v["function"] != function:
                continue
            if modality and v["modality"] != modality:
                continue
            if runner and runner not in v["runners"]:
                continue
            if ql and ql not in (v.get("label") or v["name"]).lower() and ql not in v["path"].lower():
                continue
            out.append(v)
        out.sort(key=lambda v: (not v["favorite"], not v["selected_for"], -(v.get("params_b") or 0), v["name"].lower()))
        return out[:limit]

    def counts(self):
        c = {}
        for m in self.d["models"].values():
            v = self.view(m)
            if v.get("present", True) and not v["hidden"] and not v.get("component"):
                c[v["function"] or "other"] = c.get(v["function"] or "other", 0) + 1
        return c

    def get(self, mid):
        m = self.d["models"].get(mid)
        return self.view(m) if m else None

    # ------------------------------------------------------------ the user's decisions
    def override(self, mid, **kv):
        if mid not in self.d["models"]:
            raise KeyError("no such model")
        with self.lock:
            o = self.d["overrides"].setdefault(mid, {})
            for k, v in kv.items():
                if k in ("label", "modality", "subtype", "note", "hidden", "favorite"):
                    if v in (None, "", False):
                        o.pop(k, None)
                    else:
                        o[k] = v
            if not o:
                self.d["overrides"].pop(mid, None)
            self._save()
        return self.get(mid)

    def select(self, function, mid, by="user"):
        """the model to use for a kind of work. by='user' is a pin the AI will not override; by='ai' is a choice that a later user pin replaces"""
        if function not in FUNCTIONS:
            raise ValueError("unknown function %r (one of %s)" % (function, ", ".join(FUNCTIONS)))
        if mid and mid not in self.d["models"]:
            raise KeyError("no such model")
        with self.lock:
            cur = self.d["selection"].get(function)
            if by == "ai" and cur and cur.get("by") == "user" and self.d["models"].get(cur.get("model_id"), {}).get("present", True):
                return dict(ok=False, error="pinned_by_user", message="the user chose %s for %s: the AI does not override a user's choice" % (cur["model_id"], function), current=cur)
            if not mid:
                self.d["selection"].pop(function, None)
            else:
                self.d["selection"][function] = dict(model_id=mid, by=by, ts=time.time())
            self._save()
        return dict(ok=True, selection=self.d["selection"].get(function))

    # ------------------------------------------------------------ the AI's choice
    def pick(self, function, loaded=(), max_size_gb=None, prefer_small=False):
        """-> dict(model, by, reasons, alternatives). The user's pin wins when that model is present; otherwise the best of what exists, scored on: favourite, already loaded (no load time), fits the size
        limit, newer, bigger-is-better unless prefer_small. The reasons are returned so the choice can be shown and argued with."""
        with self.lock:
            sel = self.d["selection"].get(function)
        if sel:
            m = self.get(sel["model_id"])
            if m and m.get("present", True):
                return dict(model=m, by=sel["by"], reasons=["chosen by %s" % ("the user" if sel["by"] == "user" else "an earlier AI choice")], alternatives=[])
        cands = [m for m in self.list(function=function, limit=2000)]
        if not cands:
            return dict(model=None, by=None, reasons=["no %s model found: scan, or add the folder it is in" % FUNC_LABEL.get(function, function)], alternatives=[])
        scored = []
        loaded = set(loaded)
        for m in cands:
            s, why = 0.0, []
            if m["favorite"]:
                s += 5
                why.append("a favourite")
            if m["name"] in loaded or m["path"] in loaded or m["path"].replace("ollama:", "") in loaded:
                s += 4
                why.append("already loaded")
            gb = m.get("size_gb") or 0
            if max_size_gb and gb > max_size_gb:
                s -= 100
                why.append("too big (%.0f GB > %.0f)" % (gb, max_size_gb))
            p = m.get("params_b") or 0
            s += (-p / 20.0 if prefer_small else min(p, 40) / 20.0)
            if m["source"] == "ollama":
                s += 0.5
                why.append("served by Ollama")
            s += min(1.0, (m.get("mtime") or 0) / 2e9)
            scored.append((s, m, why))
        scored.sort(key=lambda t: -t[0])
        best = scored[0]
        return dict(model=best[1], by="ai", reasons=best[2] or ["best match by size and recency"], alternatives=[dict(name=t[1]["name"], id=t[1]["id"], score=round(t[0], 2)) for t in scored[1:4]])
