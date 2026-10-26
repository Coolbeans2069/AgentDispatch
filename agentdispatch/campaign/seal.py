"""evidence: what a finished job produced, hashed and kept. Every seal copies the job's outputs into <data>/jobs/<id>/artifacts, records the hash of each input and output, the environment (tool
versions), the commands and the models, makes the record read-only, and appends it to a hash-chained evidence log. A sealed job is never rewritten: a correction is an ERRATUM appended next to it."""
import hashlib, json, os, shutil, stat, threading, time

MAX_COPY_MB = 40


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


from .state import last_line


class Evidence:
    def __init__(self, data_dir, now=time.time):
        self.dir = os.path.join(data_dir, "jobs")
        os.makedirs(self.dir, exist_ok=True)
        self.chain = os.path.join(data_dir, "evidence.jsonl")
        self.now, self.lock = now, threading.RLock()

    def job_dir(self, jid):
        return os.path.join(self.dir, jid)

    # ------------------------------------------------------------ the chain
    def _tip(self):
        try:
            with open(self.chain, "rb") as f:
                return json.loads(last_line(f))["hash"]
        except (OSError, ValueError, IndexError, KeyError):
            return "0" * 64

    def append(self, kind, job, **payload):
        with self.lock:
            rec = dict(t=self.now(), kind=kind, job=job, prev=self._tip(), **payload)
            rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            with open(self.chain, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return rec["hash"]

    def tip(self):
        return self._tip()

    def verify(self):
        prev = "0" * 64
        try:
            lines = open(self.chain, encoding="utf-8").read().splitlines()
        except OSError:
            return True, ""
        for i, l in enumerate(lines, 1):
            r = json.loads(l)
            h = r.pop("hash")
            if r.get("prev") != prev or hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != h:
                return False, "the evidence chain breaks at entry %d" % i
            prev = h
        return True, ""

    # ------------------------------------------------------------ seal / verify / erratum
    def is_sealed(self, jid):
        return os.path.isfile(os.path.join(self.job_dir(jid), "seal.json"))

    def seal(self, jid, project_dir=None, rels=(), base_dir=None, record=None, extra=None):
        """-> the seal (dict). Copies `rels` from project_dir into artifacts/, hashes them (and the matching base files = the inputs), writes seal.json read-only, appends to the chain."""
        with self.lock:
            if self.is_sealed(jid):
                return self.read_seal(jid)
            d = self.job_dir(jid)
            art = os.path.join(d, "artifacts")
            os.makedirs(art, exist_ok=True)
            outs, ins, total = [], [], 0
            for rel in sorted(set(rels)):
                src = os.path.join(project_dir or "", rel)
                if not project_dir or not os.path.isfile(src):
                    continue
                total += os.path.getsize(src)
                if total > MAX_COPY_MB * 1048576:
                    break
                dst = os.path.join(art, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                outs.append(dict(path=rel.replace("\\", "/"), sha256=sha256_file(dst), bytes=os.path.getsize(dst)))
                b = os.path.join(base_dir or "", rel)
                if base_dir and os.path.isfile(b):
                    ins.append(dict(path=rel.replace("\\", "/"), sha256=sha256_file(b)))
            for name, text in (extra or {}).items():                                           # documents made by the agency itself (result.json, the workflow)
                dst = os.path.join(art, name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                outs.append(dict(path=name, sha256=sha256_file(dst), bytes=os.path.getsize(dst)))
            seal = dict(job=jid, sealed=self.now(), outputs=outs, inputs=ins, record=record or {})
            body = json.dumps(seal, sort_keys=True, ensure_ascii=False)
            seal["seal_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
            p = os.path.join(d, "seal.json")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                json.dump(seal, f, indent=1)
            try:
                os.chmod(p, stat.S_IREAD)
            except OSError:
                pass
            seal["chain"] = self.append("seal", jid, seal_sha256=seal["seal_sha256"], outputs=len(outs))
            return seal

    def read_seal(self, jid):
        try:
            return json.load(open(os.path.join(self.job_dir(jid), "seal.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def verify_seal(self, jid):
        """-> (ok, problems): the sealed artifacts are byte-for-byte what was sealed"""
        s = self.read_seal(jid)
        if not s:
            return False, ["not sealed"]
        bad = []
        body = {k: v for k, v in s.items() if k not in ("seal_sha256", "chain")}
        if hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != s["seal_sha256"]:
            bad.append("seal.json itself was changed")
        for o in s["outputs"]:
            p = os.path.join(self.job_dir(jid), "artifacts", o["path"])
            if not os.path.isfile(p) or sha256_file(p) != o["sha256"]:
                bad.append("%s changed or is missing" % o["path"])
        return not bad, bad

    def erratum(self, jid, text, actor):
        """append a correction; the sealed record and artifacts stay exactly as they were"""
        with self.lock:
            d = self.job_dir(jid)
            os.makedirs(d, exist_ok=True)
            rec = dict(t=self.now(), actor=actor, text=(text or "")[:4000])
            with open(os.path.join(d, "errata.jsonl"), "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return self.append("erratum", jid, actor=actor, sha256=hashlib.sha256(rec["text"].encode("utf-8")).hexdigest())

    def errata(self, jid):
        try:
            return [json.loads(l) for l in open(os.path.join(self.job_dir(jid), "errata.jsonl"), encoding="utf-8").read().splitlines()]
        except OSError:
            return []
