"""cheap, dependency-free 'have we tried this already?' matching for the attempt ledger"""
import hashlib, re

_NOISE = re.compile(r"0x[0-9a-f]+|\b[0-9a-f]{8,}\b|\b\d+(?:\.\d+)*\b|[a-z]:[\\/][^\s]+|/[^\s]+/[^\s]+")
_WORD = re.compile(r"[a-z_][a-z0-9_\-]{1,}")
_STOP = set("the a an and or of to in on for with is are was were be it this that then now just please try trying using use run running again also i we you".split())


def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[:-len(suf)]
    return w


_SYN = {"app": "application", "apps": "application", "apks": "apk", "func": "function", "fn": "function", "proc": "procedure", "bin": "binary", "exe": "binary", "callers": "call", "caller": "call", "calls": "call", "called": "call", "invokes": "call"}


def tokens(text):
    t = _NOISE.sub(" ", (text or "").lower())
    return [_stem(_SYN.get(w, w)) for w in _WORD.findall(t) if w not in _STOP]


def fingerprint(text):
    """identical after normalisation (numbers, paths, hashes, filler words removed) -> identical fingerprint"""
    return hashlib.sha1(" ".join(sorted(set(tokens(text)))).encode()).hexdigest()[:16]


def similarity(a, b):
    """0..1: the average of Jaccard and the overlap coefficient over stemmed words (robust to rewording and to one text being longer)"""
    sa, sb = set(tokens(a)), set(tokens(b))
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    jac = inter / float(len(sa | sb))
    if min(len(sa), len(sb)) < 4:                                    # a very short text must not "contain" every longer one: no overlap credit
        return jac
    return 0.5 * (jac + inter / float(min(len(sa), len(sb))))
