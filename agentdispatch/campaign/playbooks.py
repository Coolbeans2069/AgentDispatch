"""playbooks: when a job ends, its whole workflow is written down as one JSON file, sorted into folders by kind / template / outcome, and indexed. The driving agent (and any new project) can search
them, read a finished workflow, look at the failure points that keep recurring, and start a new job from one instead of working it out again: that saves the thinking (and the tokens) every time
a project grows or spawns another."""
import json, os, re, threading, time

from ..similarity import similarity

SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def _slug(s, default="generic"):
    return SAFE.sub("-", (s or "").strip())[:40].strip("-") or default


def _reason_key(text):
    t = re.sub(r"[0-9a-f]{8,}|\b\d+\b", "#", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()[:110]


class Playbooks:
    def __init__(self, data_dir, now=time.time):
        self.dir = os.path.join(data_dir, "playbooks")
        os.makedirs(self.dir, exist_ok=True)
        self.index_path = os.path.join(self.dir, "index.json")
        self.now, self.lock = now, threading.RLock()
        self.index = {}
        try:
            self.index = json.load(open(self.index_path, encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def _save(self):
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.index, f, indent=1)
        os.replace(tmp, self.index_path)

    # ------------------------------------------------------------ writing one
    def record(self, pb):
        """pb: the full workflow dict (see Governor.workflow). -> its id. Rewritten (not duplicated) if the job is recorded again, e.g. after a rework."""
        with self.lock:
            pid = "PB-" + pb["job"]
            cat = "%s/%s" % (_slug(pb.get("kind")), _slug(pb.get("template")))
            pb["id"], pb["category"] = pid, cat
            folder = os.path.join(self.dir, cat.replace("/", os.sep), _slug(pb["outcome"]))
            os.makedirs(folder, exist_ok=True)
            old = self.index.get(pid)
            if old and old.get("file") and os.path.isfile(old["file"]) and os.path.normpath(old["file"]) != os.path.normpath(os.path.join(folder, pb["job"] + ".json")):
                os.remove(old["file"])
            path = os.path.join(folder, pb["job"] + ".json")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(pb, f, indent=1, ensure_ascii=False)
            self.index[pid] = dict(id=pid, job=pb["job"], title=pb["title"], goal=pb["goal"][:300], kind=pb["kind"], template=pb.get("template", ""), campaign=pb.get("campaign", ""), gate=pb.get("gate", ""),
                                   outcome=pb["outcome"], category=cat, file=path, finished=pb.get("finished"), secs=pb.get("secs"), steps=len(pb.get("steps", [])), failures=len(pb.get("failure_points", [])),
                                   tools=sorted({t["tool"] for t in pb.get("tools", [])}), agents=sorted({s.get("agent") for s in pb.get("steps", []) if s.get("agent")}))
            self._save()
            return pid

    # ------------------------------------------------------------ reading
    def get(self, pid):
        ent = self.index.get(pid) or self.index.get("PB-" + pid)
        if not ent:
            return None
        try:
            return json.load(open(ent["file"], encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def categories(self):
        c = {}
        for e in self.index.values():
            k = e["category"]
            d = c.setdefault(k, dict(category=k, total=0, ok=0, failed=0, blocked=0, other=0))
            d["total"] += 1
            d[e["outcome"] if e["outcome"] in ("ok", "failed", "blocked") else "other"] += 1
        return sorted(c.values(), key=lambda x: -x["total"])

    def search(self, query="", category="", outcome="", campaign="", tool="", limit=20):
        q = (query or "").strip()
        out = []
        for e in self.index.values():
            if category and not e["category"].startswith(category):
                continue
            if outcome and e["outcome"] != outcome:
                continue
            if campaign and e.get("campaign") != campaign:
                continue
            if tool and tool not in e.get("tools", []):
                continue
            score = similarity(q, e["title"] + " " + e["goal"]) if q else 0.0
            if q and score < 0.12 and q.lower() not in (e["title"] + e["goal"]).lower():
                continue
            out.append((score, e.get("finished") or 0, e))
        out.sort(key=lambda x: (-x[0], -x[1]))
        return [dict(e, score=round(s, 2)) for s, _, e in out[:max(1, min(100, int(limit)))]]

    def failures(self, category="", limit=15):
        """the failure points that recur: grouped by what went wrong (numbers and hashes folded together), with counts, where and which playbooks"""
        groups = {}
        for e in self.index.values():
            if category and not e["category"].startswith(category):
                continue
            pb = self.get(e["id"])
            for f in (pb or {}).get("failure_points", []):
                key = (f.get("kind", ""), _reason_key(f.get("reason", "")))
                g = groups.setdefault(key, dict(kind=f.get("kind", ""), reason=f.get("reason", "")[:240], count=0, playbooks=[], agents=set(), where=set()))
                g["count"] += 1
                if e["id"] not in g["playbooks"]:
                    g["playbooks"].append(e["id"])
                if f.get("agent"):
                    g["agents"].add(f["agent"])
                if f.get("where"):
                    g["where"].add(f["where"])
        out = [dict(g, agents=sorted(g["agents"]), where=sorted(g["where"]), playbooks=g["playbooks"][:6]) for g in groups.values()]
        out.sort(key=lambda g: -g["count"])
        return out[:limit]

    def suggest(self, goal, kind="", limit=3):
        """for a new goal: the most similar playbooks that WORKED (their steps, tools, route), plus the failure points seen on similar work - what a new job should start from"""
        hits = [h for h in self.search(goal, outcome="ok", limit=20) if not kind or h["kind"] == kind][:limit]
        out = []
        for h in hits:
            pb = self.get(h["id"]) or {}
            out.append(dict(id=h["id"], score=h["score"], title=h["title"], steps=[dict(objective=s["objective"], needs=s.get("needs"), agent=s.get("agent"), tool=s.get("tool")) for s in pb.get("steps", [])],
                            tools=h["tools"], acceptance=pb.get("acceptance"), meta=pb.get("meta"), lessons=pb.get("lessons", [])))
        related = [h for h in self.search(goal, limit=12) if h["outcome"] in ("failed", "blocked")][:3]
        warn = []
        for h in related:
            pb = self.get(h["id"]) or {}
            warn += [dict(playbook=h["id"], where=f.get("where"), reason=f.get("reason", "")[:200]) for f in pb.get("failure_points", [])][:3]
        return dict(similar_successes=out, warnings=warn[:8])

    def export(self):
        """everything as one object (to carry the library to another project)"""
        return dict(exported=self.now(), playbooks=[self.get(i) for i in self.index if self.get(i)])

    def import_(self, bundle, prefix="IMP-"):
        n = 0
        for pb in (bundle or {}).get("playbooks", []):
            if not isinstance(pb, dict) or "job" not in pb or "outcome" not in pb:
                continue
            pb = dict(pb, job=prefix + str(pb["job"]), imported=True)
            self.record(pb)
            n += 1
        return n
