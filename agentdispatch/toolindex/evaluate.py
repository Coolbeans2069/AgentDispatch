"""known-answer evaluation of an index: for each golden query, is an acceptable tool in the top k?"""
import json

from .search import Index


def evaluate(index, schemas, golden, ks=(1, 3, 5)):
    ix = Index(index, schemas)
    hits = {k: 0 for k in ks}
    misses = []
    for g in golden:
        ranked = [r["tool"] for r in ix.search(g["q"], limit=max(ks))]
        pos = next((i for i, t in enumerate(ranked) if t in g["any"]), None)
        for k in ks:
            if pos is not None and pos < k:
                hits[k] += 1
        if pos is None or pos >= 3:
            misses.append((g["q"], ranked[:3]))
    n = float(len(golden)) or 1.0
    return dict(n=len(golden), **{"hit@%d" % k: round(hits[k] / n, 2) for k in ks}), misses
