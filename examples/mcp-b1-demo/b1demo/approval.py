"""Preview -> single-use token -> commit, for the one write tool.

A token is bound to a SHA-256 hash of the exact (normalised) payload, expires after TTL seconds and can be used once.
Only a hash of the token is stored."""
import hashlib
import json
import secrets
import time

TTL = 600   # 10 minutes


class ApprovalError(Exception):
    pass


def payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Approvals:
    def __init__(self, repo, clock=time.time, ttl=TTL):
        self.repo, self.clock, self.ttl = repo, clock, ttl

    def issue(self, payload):
        token = secrets.token_urlsafe(18)
        expires = self.clock() + self.ttl
        with self.repo.lock:
            self.repo.con.execute("INSERT INTO approval_tokens VALUES (?, ?, ?, 'issued')", (_h(token), payload_hash(payload), expires))
        return token, expires

    def consume(self, token, payload):
        """Spend `token` for `payload`; raise ApprovalError (and leave nothing written) when it is not valid."""
        if not token or not isinstance(token, str):
            raise ApprovalError("no approval_token: call once without one to get a preview and a token")
        with self.repo.lock:
            row = self.repo.con.execute("SELECT payload_hash, expires_at, state FROM approval_tokens WHERE token_hash = ?", (_h(token),)).fetchone()
            if row is None:
                raise ApprovalError("unknown approval_token")
            if row["state"] != "issued":
                raise ApprovalError("approval_token already %s; request a new preview" % row["state"])
            if self.clock() > row["expires_at"]:
                self.repo.con.execute("UPDATE approval_tokens SET state = 'expired' WHERE token_hash = ?", (_h(token),))
                raise ApprovalError("approval_token expired (tokens last %d minutes); request a new preview" % (self.ttl // 60))
            if row["payload_hash"] != payload_hash(payload):
                raise ApprovalError("the request changed since the preview; the token only approves the exact previewed payload")
            cur = self.repo.con.execute("UPDATE approval_tokens SET state = 'used' WHERE token_hash = ? AND state = 'issued'", (_h(token),))
            if cur.rowcount != 1:
                raise ApprovalError("approval_token already used")

    def decline(self, token):
        with self.repo.lock:
            cur = self.repo.con.execute("UPDATE approval_tokens SET state = 'declined' WHERE token_hash = ? AND state = 'issued'", (_h(token or ""),))
        return cur.rowcount == 1
