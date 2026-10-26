"""OpenRouter free-tier models: catalog entries, API key lookup, and small helpers shared by the router, agency and Models page.

Key: the environment variable OPENROUTER_API_KEY only (never stored in the data folder or the repo).

API: https://openrouter.ai/api/v1  (OpenAI-compatible chat completions). Free models end in :free and share one
account quota (20 requests/min, 50/day until the account has bought $10 of credits, then 1000/day)."""
import hashlib, os, re

API_URL = "https://openrouter.ai/api/v1"
# Account-wide free caps (OpenRouter docs 2026). Engine room uses these for /budget.
RPM, TPM, RPD = 20, 200000, 50
RPD_CREDITED = 1000                                                    # after a one-time >= $10 credit purchase

# id, short label, modality, subtype, strengths hint for the brain / agency, colour for Engine Room bars
FREE_MODELS = [
    ("openrouter/free", "OpenRouter free (auto)", "text", "chat", ["fast", "write", "research"], "#00d4aa"),
    ("nvidia/nemotron-3.5-super-vl:free", "Nemotron 3.5 Super VL", "vision", "vlm", ["vision", "write", "research"], "#76b900"),
    ("nvidia/nemotron-3.5-lightning:free", "Nemotron 3.5 Lightning", "text", "chat", ["fast", "write"], "#a4e509"),
    ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron 3 Super 120B", "text", "chat", ["write", "research", "plan", "long-context"], "#5dff9a"),
    ("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra 550B", "text", "reasoning", ["reasoning", "plan", "research", "long-context"], "#3ddc84"),
    ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "Nemotron 3 Nano Omni reason", "text", "reasoning", ["reasoning", "plan", "write"], "#19f0c8"),
    ("nvidia/nemotron-3.5-content-safety:free", "Nemotron 3.5 Content Safety", "text", "chat", ["review", "write"], "#7fb5c8"),
    ("google/gemma-4-26b-a4b-it:free", "Gemma 4 26B A4B", "text", "chat", ["write", "research", "fast"], "#5dff9a"),
    ("google/gemma-4-31b-it:free", "Gemma 4 31B", "text", "chat", ["write", "research", "plan"], "#4ee9ff"),
    ("cohere/north-mini-code:free", "North Mini Code", "text", "code", ["code", "fast"], "#4da3ff"),
    ("xingchen-agi/xing4.0-29b-a4b:free", "Xing4.0 29B A4B", "text", "chat", ["write", "research"], "#a98bff"),
    ("thinkingmachines/inkling:free", "Inkling", "text", "chat", ["write", "research", "plan"], "#ff9a1a"),
    ("thinkingmachines/inkling-small:free", "Inkling Small", "text", "chat", ["fast", "write"], "#ffd34d"),
    ("poolside/laguna-s-2.1:free", "Laguna S 2.1", "text", "code", ["code", "write", "plan"], "#ff6bb5"),
    ("poolside/laguna-xs-2.1:free", "Laguna XS 2.1", "text", "code", ["code", "fast"], "#ff5ad1"),
    ("liquid/lfm-2.5-2.6b:free", "LFM 2.5 2.6B", "text", "chat", ["fast", "write"], "#e0ae52"),
    ("dots-studio/dots-3-note-preview:free", "Dots3 Note Preview", "text", "chat", ["write", "research", "long-context"], "#c28bff"),
]

IDS = [m[0] for m in FREE_MODELS]
BY_ID = {m[0]: m for m in FREE_MODELS}
DEFAULT_CLOUD = "openrouter/free"
# Prefer these when auto-picking a concrete free model (auto router first, then strong general, then code)
# Mid free first (rate-limit friendly); ultra last. Nemotron super/nano for hard free-zone repairs.
PREFERRED = ["openrouter/free", "nvidia/nemotron-3.5-lightning:free", "thinkingmachines/inkling-small:free",
             "cohere/north-mini-code:free", "google/gemma-4-26b-a4b-it:free", "thinkingmachines/inkling:free",
             "google/gemma-4-31b-it:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
             "nvidia/nemotron-3-super-120b-a12b:free", "poolside/laguna-s-2.1:free",
             "nvidia/nemotron-3-ultra-550b-a55b:free"]


def is_openrouter_id(model):
    m = (model or "").strip()
    if not m:
        return False
    if m in BY_ID or m.startswith("openrouter/"):
        return True
    if m.endswith(":free") and "/" in m:
        return True
    return False


def api_key(data_dir=None):
    """the OpenRouter key from the environment (OPENROUTER_API_KEY); data_dir is accepted for compatibility and ignored"""
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip()


def headers(key, data_dir=None):
    """OpenRouter asks for HTTP-Referer / X-Title so dashboards can name the app; Authorization is the key."""
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:8870", "X-Title": "AgentDispatch"}


def catalog_entry(mid, label, modality, subtype, now):
    path = "openrouter:" + mid
    return dict(id=hashlib.sha1(path.encode()).hexdigest()[:10], name=mid, label=label, path=path, source="openrouter",
                format="api", modality=modality, subtype=subtype, params_b=None, quant="", size_gb=0, size_class="cloud",
                runners=["openrouter"], component=None, drive="cloud", mtime=0, meta=dict(provider="openrouter", free=True),
                first_seen=now, last_seen=now, present=True)


def ensure_in_catalog(catalog):
    """upsert every free OpenRouter model into the Models catalog so /models and /brain can see them. Keeps user overrides."""
    import time
    now = time.time()
    with catalog.lock:
        for mid, label, modality, subtype, _strengths, _color in FREE_MODELS:
            e = catalog_entry(mid, label, modality, subtype, now)
            old = catalog.d["models"].get(e["id"], {})
            e["first_seen"] = old.get("first_seen", now)
            e["label"] = label
            catalog.d["models"][e["id"]] = e
            # also keep a stable override label so a rescan does not blank the display name
            ov = catalog.d.setdefault("overrides", {}).setdefault(e["id"], {})
            ov.setdefault("label", label)
        catalog._save()
    return len(FREE_MODELS)


def strengths_of(mid):
    row = BY_ID.get(mid)
    return list(row[4]) if row else ["write", "research"]


def color_of(mid):
    row = BY_ID.get(mid)
    return row[5] if row else "#00d4aa"


def short_label(mid):
    row = BY_ID.get(mid)
    return row[1] if row else mid.split("/")[-1]
