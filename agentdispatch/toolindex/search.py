"""find the right tool without reading the catalog: a tiny BM25-style ranking over names, cards, group hints, keywords and example requests"""
import math, re

from ..similarity import tokens


class Index:
    def __init__(self, index, schemas, full_schemas=None, backend="rea"):
        self.backend, self.index, self.schemas, self.full = backend, index, schemas, full_schemas or {}
        self.docs = {}
        df = {}
        for name, t in index["tools"].items():
            g = index["groups"].get(t["group"], {})
            h = t.get("hints") or {}
            fields = [(tokens(name.replace("_", " ")), 3.0), (tokens(t["card"]), 1.0), (tokens(t.get("blurb", "")), 0.6), (tokens(" ".join(h.get("keywords", []))), 2.0), (tokens(" ".join(h.get("examples", []))), 1.5),
                      (tokens(h.get("use_when", "") + " " + h.get("topic", "").replace("-", " ")), 1.0), (tokens(" ".join(g.get("topics", [])).replace("-", " ")), 0.4)]
            tf = {}
            for toks, w in fields:
                for tok in toks:
                    tf[tok] = tf.get(tok, 0.0) + w
            self.docs[name] = tf
            for tok in tf:
                df[tok] = df.get(tok, 0) + 1
        n = max(1, len(self.docs))
        self.idf = {tok: math.log(1 + (n - d + 0.5) / (d + 0.5)) for tok, d in df.items()}

    def groups(self):
        """L0: the whole catalog in about 500 tokens"""
        return [dict(group=g, summary=v.get("summary", ""), when_to_use=v.get("when_to_use", ""), topics=v.get("topics", [])[:6], tools=len(v["tools"])) for g, v in sorted(self.index["groups"].items())]

    def search(self, query="", group=None, limit=8):
        names = list(self.index["groups"][group]["tools"]) if group in self.index["groups"] else list(self.docs)
        q = tokens(query)
        if not q:
            ranked = [(0.0, n) for n in sorted(names)]
        else:
            ranked = []
            for n in names:
                tf = self.docs[n]
                s = sum(self.idf.get(t, 0.0) * (tf[t] / (tf[t] + 1.2)) for t in q if t in tf)
                if s > 0:
                    ranked.append((s, n))
            ranked.sort(key=lambda x: (-x[0], x[1]))
        out = []
        for s, n in ranked[:max(1, min(30, int(limit)))]:
            t = self.index["tools"][n]
            out.append(dict(tool=n, card=t["card"], group=t["group"], risk=t["risk"], score=round(s, 2)))
        return out

    def describe(self, name, full=False):
        t = self.index["tools"].get(name)
        if not t:
            return None
        if full and name in self.full:
            return dict(tool=name, risk=t["risk"], schema=self.full[name], note="full original schema")
        d = dict(tool=name, card=t["card"], risk=t["risk"], schema=self.schemas.get(name, {}))
        if t.get("cut_level"):
            d["note"] = "large schema shortened (nested parts hidden); call tool_describe with full=true for the original"
        return d
