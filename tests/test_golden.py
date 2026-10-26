"""retrieval quality regression on REA's real catalog (skipped when the catalog dump is not present): known-answer queries must find an acceptable tool"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.toolindex.build import build_index
from agentdispatch.toolindex.evaluate import evaluate

CAT = os.environ.get("REA_CATALOG", "")                                   # path to a dump of the REA tool catalog (optional)
G = json.load(open(os.path.join(ROOT, "tests", "golden_rea.json"), encoding="utf-8"))
HARD = json.load(open(os.path.join(ROOT, "tests", "golden_rea_hard.json"), encoding="utf-8"))      # 20 paraphrased, ambiguous requests: where a better hint writer shows


def test_deterministic_index_quality():
    if not os.path.isfile(CAT):
        print("skip: no catalog dump"); return
    T = json.load(open(CAT, encoding="utf-8"))
    idx, sch = build_index(T, "rea", use_llm=False, log=lambda *a: None)
    r, miss = evaluate(idx, sch, G)
    print("deterministic:", r, "| hard:", evaluate(idx, sch, HARD)[0])
    assert r["hit@3"] >= 0.85 and r["hit@1"] >= 0.70, r
    c = idx["stats"]
    assert c["compact_tokens_est"] * 6 < c["raw_tokens_est"], c            # the whole compact catalog is at least 6x smaller than REA's own list


def test_saved_model_assisted_index_quality():
    p = os.path.join(ROOT, "data", "index", "rea", "index.json")
    if not os.path.isfile(p):
        print("skip: no built index"); return
    idx = json.load(open(p, encoding="utf-8"))
    sch = json.load(open(os.path.join(ROOT, "data", "index", "rea", "schemas.json"), encoding="utf-8"))
    r, miss = evaluate(idx, sch, G)
    hard, hmiss = evaluate(idx, sch, HARD)
    print("model-assisted (%s):" % idx["llm"].get("model"), r, "| hard:", hard, "misses:", miss)
    assert r["hit@3"] >= 0.90 and r["hit@1"] >= 0.80, r
    assert hard["hit@3"] >= 0.85 if "gemma" in str(idx["llm"].get("model")) else hard["hit@3"] >= 0.60, hard


if __name__ == "__main__":
    test_deterministic_index_quality(); test_saved_model_assisted_index_quality()
    print("ALL GOLDEN TESTS PASSED")
