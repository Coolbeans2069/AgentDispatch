"""Append-only audit log: an SQLite table (UPDATE and DELETE are refused by triggers) mirrored to audit.jsonl."""
import datetime as dt
import hashlib
import json
import os
import re

_SECRET_KEYS = re.compile(r"(token|secret|password|passwd|api_?key|authorization|cookie)", re.I)


def redact(value):
    """Copy of `value` with secret-looking fields masked. Approval tokens keep a 4-character prefix so entries can be matched."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _SECRET_KEYS.search(str(k)) and isinstance(v, str) and v:
                out[k] = v[:4] + "...(redacted)"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def result_hash(result):
    return hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()


class AuditLog:
    def __init__(self, repo):
        self.repo = repo
        self.jsonl = os.path.join(repo.data_dir, "audit.jsonl")

    def record(self, tool, args, result, approval="n/a", caller="unknown", ok=True):
        entry = dict(ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), tool=tool, args=redact(args or {}),
                     result_hash=result_hash(result), approval=approval, caller=caller, ok=bool(ok))
        with self.repo.lock:
            cur = self.repo.con.execute("INSERT INTO audit_log (ts, tool, args, result_hash, approval, caller, ok) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                        (entry["ts"], tool, json.dumps(entry["args"], sort_keys=True), entry["result_hash"],
                                         approval, caller, int(bool(ok))))
            entry = dict(id=cur.lastrowid, **entry)
            with open(self.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def tail(self, n=20):
        n = max(1, min(int(n), 500))
        with self.repo.lock:
            rows = self.repo.con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(id=r["id"], ts=r["ts"], tool=r["tool"], args=json.loads(r["args"]), result_hash=r["result_hash"],
                     approval=r["approval"], caller=r["caller"], ok=bool(r["ok"])) for r in reversed(rows)]
