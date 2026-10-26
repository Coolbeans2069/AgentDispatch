"""secret scrubbing for everything that goes on the board (posts, replies, attachments, attempt evidence)"""
import re

_KEY = r"(?:api[_\-]?key|secret|token|passwd|password|authorization|credentials?)\w*"

PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("google-key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("xai-key", re.compile(r"\bxai-[A-Za-z0-9]{20,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}")),
    ("basic-auth", re.compile(r"(?i)\b(?:basic|digest)\s+[A-Za-z0-9+/=]{12,}")),
    # key = value, key: value, "key": "value", 'key':'value' (JSON / YAML / env / headers); the VALUE is replaced, the key stays readable
    ("assignment", re.compile(r"""(?i)(\b%s["']?\s*[:=]\s*)(["']?)(?!\[REDACTED)([^\s"',}\]]{6,})""" % _KEY)),
]


def redact(text):
    """-> (clean text, sorted list of the kinds of secret that were removed)"""
    found = set()
    out = text or ""
    for kind, pat in PATTERNS:
        def sub(m, kind=kind):
            found.add(kind)
            if kind == "assignment":
                return "%s%s[REDACTED:assignment]" % (m.group(1), m.group(2))
            return "[REDACTED:%s]" % kind
        out = pat.sub(sub, out)
    return out, sorted(found)
