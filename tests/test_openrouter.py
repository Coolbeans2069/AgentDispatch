"""OpenRouter free-tier helpers: key lookup, catalog upsert, id detection."""
import os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentdispatch import openrouter as OR
from agentdispatch.models.catalog import Catalog


class TestOpenRouter(unittest.TestCase):
    def test_free_models_cover_user_list(self):
        want = {
            "nvidia/nemotron-3.5-super-vl:free", "openrouter/free", "xingchen-agi/xing4.0-29b-a4b:free",
            "dots-studio/dots-3-note-preview:free", "liquid/lfm-2.5-2.6b:free", "nvidia/nemotron-3.5-lightning:free",
            "thinkingmachines/inkling-small:free", "poolside/laguna-s-2.1:free", "thinkingmachines/inkling:free",
            "poolside/laguna-xs-2.1:free", "nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3.5-content-safety:free",
            "cohere/north-mini-code:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "google/gemma-4-26b-a4b-it:free", "google/gemma-4-31b-it:free", "nvidia/nemotron-3-super-120b-a12b:free",
        }
        self.assertTrue(want.issubset(set(OR.IDS)), sorted(want - set(OR.IDS)))

    def test_is_openrouter_id(self):
        self.assertTrue(OR.is_openrouter_id("openrouter/free"))
        self.assertTrue(OR.is_openrouter_id("google/gemma-4-31b-it:free"))
        self.assertFalse(OR.is_openrouter_id("gemini-3.5-flash-lite"))
        self.assertFalse(OR.is_openrouter_id("local-qwen3-4b"))

    def test_api_key_comes_only_from_the_environment(self):
        d = tempfile.mkdtemp(prefix="or_key_")
        with open(os.path.join(d, "openrouter.key"), "w", encoding="utf-8") as f:
            f.write("placeholder-from-file\n")                       # a key file in the data folder is ignored
        old = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            self.assertEqual(OR.api_key(d), "")
            os.environ["OPENROUTER_API_KEY"] = "placeholder-from-env"
            self.assertEqual(OR.api_key(d), "placeholder-from-env")
        finally:
            if old is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = old

    def test_ensure_in_catalog(self):
        d = tempfile.mkdtemp(prefix="or_cat_")
        cat = Catalog(d)
        n = OR.ensure_in_catalog(cat)
        self.assertEqual(n, len(OR.FREE_MODELS))
        names = {m["name"] for m in cat.d["models"].values() if m.get("source") == "openrouter"}
        self.assertEqual(names, set(OR.IDS))
        self.assertTrue(all(m.get("runners") == ["openrouter"] for m in cat.d["models"].values() if m.get("source") == "openrouter"))

    def test_registry_and_ladder(self):
        from agentdispatch.agency.registry import DEFAULTS
        from agentdispatch.agency.routing import DEFAULTS as RDEF
        a = DEFAULTS["openrouter"]
        self.assertEqual(a["run"]["via"], "router")
        self.assertIn("openrouter/free", a["run"]["models"])
        self.assertIn("poolside/laguna-xs-2.1:free", a["run"]["models"])
        ladder = RDEF["ladders"]["openrouter"]
        ids = [r["model"] for r in ladder]
        self.assertIn("openrouter/free", ids)
        self.assertIn("nvidia/nemotron-3-ultra-550b-a55b:free", ids)


if __name__ == "__main__":
    unittest.main()
