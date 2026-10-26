"""the campaign state: the gates of a long scientific programme, what each one is (open, closed, locked behind others), who may move it, and the append-only, hash-chained log of every change.
A job never edits this: it PROPOSES a transition, and the state moves only through `approve` (the owner) or an automatic rule the owner switched on for that gate. The state can be filled from the
status blocks a project already keeps in its hand-off files (lines like `G06 = OPEN`), so nothing has to be retyped."""
import hashlib, json, os, re, threading, time

CLOSED_PREFIXES = ("CLOSED", "PASS", "DONE", "CERTIFIED", "PROMOTED")
STATUS_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9 _\-./()+*]{0,60}?)\s*=\s*(.*)$")


def is_closed(status):
    s = (status or "").upper().strip()
    return s.startswith(CLOSED_PREFIXES)


def status_head(value):
    """'CLOSED-AUDITED — EXACT ZERO-CONNECTED ...' -> ('CLOSED-AUDITED', 'EXACT ZERO-...')"""
    v = (value or "").strip().strip("`")
    m = re.match(r"^([A-Za-z0-9_\-./]+)\s*(?:[—–:-]\s*)?(.*)$", v, re.S)
    return (m.group(1), m.group(2).strip()) if m else (v, "")



def last_line(f, block=8192):
    """the last non-empty line of an open binary file, however long it is (a fixed-size tail cut a long claim in half and the hash chain restarted from zeros)"""
    f.seek(0, 2)
    end = f.tell()
    data, pos = b"", end
    while pos > 0:
        pos = max(0, pos - block)
        f.seek(pos)
        data = f.read(end - pos if not data else block) + data
        lines = data.rstrip().split(b"\n")
        if len(lines) > 1 or pos == 0:
            return lines[-1].decode("utf-8", "replace").strip()
    return ""

class CampaignState:
    def __init__(self, data_dir, now=time.time):
        self.path = os.path.join(data_dir, "campaign.json")
        self.log_path = os.path.join(data_dir, "campaign_log.jsonl")
        self.now, self.lock = now, threading.RLock()
        self.d = dict(version=1, campaigns={}, proposals=[], run_approvals={})
        try:
            self.d.update(json.load(open(self.path, encoding="utf-8")))
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------ storage and the chain
    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.d, f, indent=1)
        os.replace(tmp, self.path)

    def _tip(self):
        try:
            with open(self.log_path, "rb") as f:
                last = last_line(f)
            return json.loads(last)["hash"]
        except (OSError, ValueError, IndexError, KeyError):
            return "0" * 64

    def _log(self, **rec):
        rec.update(t=self.now(), prev=self._tip())
        rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        with open(self.log_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec["hash"]

    def verify_log(self):
        """-> (ok, problem): the chain of changes is intact"""
        prev = "0" * 64
        try:
            lines = open(self.log_path, encoding="utf-8").read().splitlines()
        except OSError:
            return True, ""
        for i, l in enumerate(lines, 1):
            r = json.loads(l)
            h = r.pop("hash")
            if r.get("prev") != prev or hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != h:
                return False, "the log breaks at entry %d" % i
            prev = h
        return True, ""

    def log(self, limit=60, campaign=None):
        out = []
        try:
            for l in open(self.log_path, encoding="utf-8").read().splitlines()[-400:]:
                r = json.loads(l)
                if not campaign or r.get("campaign") == campaign:
                    out.append(r)
        except OSError:
            pass
        return out[-limit:]

    # ------------------------------------------------------------ campaigns and gates
    def campaign(self, cid, create=False, title="", authority="NONE"):
        with self.lock:
            c = self.d["campaigns"].get(cid)
            if not c and create:
                c = self.d["campaigns"][cid] = dict(id=cid, title=title or cid, authority=authority, gates={}, created=self.now())
                self._log(action="campaign", campaign=cid, actor="owner", why=title)
                self._save()
            return c

    def define_gate(self, cid, gid, title="", depends_on=None, status=None, order=None, requires_human=True, auto_promote=False, note="", actor="owner", unlocks=None):
        with self.lock:
            c = self.campaign(cid, create=True)
            g = c["gates"].get(gid)
            if not g:
                g = c["gates"][gid] = dict(id=gid, title=title or gid, status=status or "NOT_RUN", depends_on=[], order=order if order is not None else len(c["gates"]) + 1, requires_human=True,
                                           auto_promote=False, note="", updated=self.now(), unlocks=[])
                self._log(action="define", campaign=cid, gate=gid, to=g["status"], actor=actor)
            if title:
                g["title"] = title
            if depends_on is not None:
                g["depends_on"] = list(depends_on)
            if unlocks is not None:
                g["unlocks"] = list(unlocks)
            if order is not None:
                g["order"] = order
            g["requires_human"], g["auto_promote"] = bool(requires_human), bool(auto_promote)
            if note:
                g["note"] = note[:600]
            if status and status != g["status"]:
                self._set(c, g, status, actor, why="defined with status")
            self._save()
            return g

    def _set(self, c, g, to, actor, job=None, why="", evidence=None):
        frm = g["status"]
        g["status"], g["updated"] = to, self.now()
        self._log(action="transition", campaign=c["id"], gate=g["id"], frm=frm, to=to, actor=actor, job=job, why=(why or "")[:300], evidence=evidence)

    def transition(self, cid, gid, to, actor="owner", job=None, why="", evidence=None):
        with self.lock:
            c = self.campaign(cid)
            if not c or gid not in c["gates"]:
                raise KeyError("no such gate: %s/%s" % (cid, gid))
            g = c["gates"][gid]
            self._set(c, g, to, actor, job, why, evidence)
            self._save()
            return g

    # ------------------------------------------------------------ dependencies
    def gate_closed(self, cid, gid, need="CLOSED"):
        c = self.campaign(cid)
        g = (c or {}).get("gates", {}).get(gid)
        if not g:
            return False
        s = (g["status"] or "").upper()
        return s.startswith((need or "CLOSED").upper()) if need and need.upper() != "CLOSED" else is_closed(g["status"])

    def unmet(self, cid, gate_reqs):
        """-> [gate ids whose required status is not reached] for {gate: required status prefix}"""
        return [gid for gid, need in (gate_reqs or {}).items() if not self.gate_closed(cid, gid, need)]

    def gate_view(self, cid):
        """gates in order with a derived state: closed | open (ready to work on) | locked (waiting for gates) | candidate"""
        c = self.campaign(cid)
        if not c:
            return []
        out = []
        for g in sorted(c["gates"].values(), key=lambda x: (x["order"], x["id"])):
            waiting = [d for d in g["depends_on"] if not self.gate_closed(cid, d)]
            s = g["status"]
            derived = "closed" if is_closed(s) else "locked" if waiting else "candidate" if (s or "").upper().startswith("CANDIDATE") else "open"
            out.append(dict(g, derived=derived, waiting_for=waiting, proposals=[p["id"] for p in self.d["proposals"] if p["state"] == "pending" and p["campaign"] == cid and p["gate"] == g["id"]]))
        return out

    def next_open(self, cid):
        """the earliest unclosed gate whose dependencies are all closed: the one thing the campaign should be working on"""
        for g in self.gate_view(cid):
            if g["derived"] in ("open", "candidate"):
                return g
        return None

    # ------------------------------------------------------------ proposals: how a job asks to move a gate
    def propose(self, cid, gid, to, job, actor, why="", evidence=None, claim=""):
        with self.lock:
            c = self.campaign(cid)
            if not c or gid not in c["gates"]:
                return dict(ok=False, error="no_gate", message="no such gate: %s/%s" % (cid, gid))
            for p in self.d["proposals"]:
                if p["state"] == "pending" and p["campaign"] == cid and p["gate"] == gid and p["to"] == to and p["job"] == job:
                    return dict(ok=True, proposal=p, note="already proposed")
            p = dict(id="P-%04d" % (len(self.d["proposals"]) + 1), campaign=cid, gate=gid, to=to, job=job, by=actor, why=(why or "")[:400], evidence=evidence, claim=claim, state="pending", created=self.now())
            self.d["proposals"].append(p)
            self._log(action="propose", campaign=cid, gate=gid, to=to, job=job, actor=actor, why=why[:200], claim=claim)
            g = c["gates"][gid]
            auto = g.get("auto_promote") and not g.get("requires_human") and c.get("authority") == "PROMOTION_ALLOWED" and claim != "INDEPENDENTLY_CONFIRMED"
            self._save()
        if auto:
            return self.decide(p["id"], True, "auto (the owner allowed automatic promotion for %s)" % gid)
        return dict(ok=True, proposal=p)

    def pending(self):
        return [p for p in self.d["proposals"] if p["state"] == "pending"]

    def decide(self, pid, approve, actor="owner", why=""):
        with self.lock:
            p = next((x for x in self.d["proposals"] if x["id"] == pid), None)
            if not p or p["state"] != "pending":
                return dict(ok=False, error="not_pending", message="no such pending proposal")
            p["state"], p["decided_by"], p["decided"] = ("approved" if approve else "rejected"), actor, self.now()
            if approve:
                self._set(self.campaign(p["campaign"]), self.campaign(p["campaign"])["gates"][p["gate"]], p["to"], actor, p["job"], why or p["why"], p.get("evidence"))
            else:
                self._log(action="reject", campaign=p["campaign"], gate=p["gate"], to=p["to"], job=p["job"], actor=actor, why=why[:200])
            self._save()
            return dict(ok=True, proposal=p)

    # ------------------------------------------------------------ import: the status blocks a project already keeps
    def import_blocks(self, cid, text, source="import", actor="import"):
        """read lines like `G06 = OPEN` (inside ``` fences or not) and make / update the gates. Continuation lines belong to the previous value. -> (created, changed)"""
        pairs, cur = [], None
        in_fence = False
        for raw in (text or "").splitlines():
            line = raw.rstrip()
            if line.strip().startswith("```"):
                in_fence = not in_fence
                cur = None
                continue
            m = STATUS_LINE.match(line) if (in_fence or re.match(r"^[A-Z0-9][A-Z0-9 _\-./()+*]{0,60}\s*=", line)) else None
            if m and not line.startswith((" ", "\t")) and m.group(1).strip().upper() == m.group(1).strip() and not m.group(1).strip().isdigit():
                cur = [m.group(1).strip(), m.group(2).strip()]
                pairs.append(cur)
            elif cur is not None and line.strip() and in_fence:
                cur[1] = (cur[1] + " " + line.strip()).strip()
            else:
                cur = None
        created = changed = 0
        with self.lock:
            c = self.campaign(cid, create=True)
            for key, val in pairs:
                st, note = status_head(val)
                if not st:
                    continue
                g = c["gates"].get(key)
                if not g:
                    g = c["gates"][key] = dict(id=key, title=key, status=st, depends_on=[], order=len(c["gates"]) + 1, requires_human=True, auto_promote=False, note=note[:600], updated=self.now(), unlocks=[],
                                               source=source)
                    self._log(action="define", campaign=cid, gate=key, to=st, actor=actor, why="imported from " + source)
                    created += 1
                elif g["status"] != st:
                    self._set(c, g, st, actor, why="imported from " + source)
                    g["note"] = note[:600] or g.get("note", "")
                    changed += 1
            self._save()
        return created, changed

    def load_pipeline(self, spec, actor="owner"):
        """create a campaign with its gates, dependencies and authority from a JSON description (see examples/)"""
        cid = spec["id"]
        with self.lock:
            c = self.campaign(cid, create=True, title=spec.get("title", cid), authority=spec.get("authority", "NONE"))
            c["authority"] = spec.get("authority", c.get("authority", "NONE"))
            for i, g in enumerate(spec.get("gates", []), 1):
                self.define_gate(cid, g["id"], g.get("title", g["id"]), g.get("depends_on", []), g.get("status"), g.get("order", i), g.get("requires_human", True), g.get("auto_promote", False), g.get("note", ""), actor,
                                 g.get("unlocks"))
            self._save()
        return self.gate_view(cid)

    # ------------------------------------------------------------ the owner's go-ahead to run a restricted job
    def approve_run(self, job_id, actor="owner"):
        with self.lock:
            self.d.setdefault("run_approvals", {})[job_id] = dict(by=actor, t=self.now())
            self._log(action="approve_run", job=job_id, actor=actor)
            self._save()

    def run_approved(self, job_id):
        return job_id in self.d.get("run_approvals", {})

    def snapshot(self):
        out = []
        for cid, c in self.d["campaigns"].items():
            gv = self.gate_view(cid)
            nxt = self.next_open(cid)
            out.append(dict(id=cid, title=c["title"], authority=c["authority"], gates=gv, next=nxt["id"] if nxt else None,
                            counts={k: sum(1 for g in gv if g["derived"] == k) for k in ("closed", "open", "locked", "candidate")}))
        return dict(campaigns=out, pending=self.pending(), chain_ok=self.verify_log()[0])
