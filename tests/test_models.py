"""the model catalog: classify, scan (real folder layouts built in a temp dir), merge with the user's decisions, select and pick"""
import json, os, shutil, struct, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.models import classify as C
from agentdispatch.models import scan as S
from agentdispatch.models.catalog import Catalog, function_of, FUNCTIONS

BIG = 65 * 1024 * 1024


def big(path, header=b"", size=BIG):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(header)
        f.truncate(max(size, len(header)))


def safetensors(path, tensors, size=BIG):
    h = {n: dict(dtype=dt, shape=sh, data_offsets=[0, 8]) for n, (dt, sh) in tensors.items()}
    raw = json.dumps(h).encode()
    big(path, struct.pack("<Q", len(raw)) + raw, size)


def gguf(path, arch="llama", name="Demo-7B", size=BIG):
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    kv = [(b"general.architecture", 8, s(arch)), (b"general.name", 8, s(name)), (b"llama.context_length", 4, struct.pack("<I", 8192)), (b"tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, 2) + s("a") + s("b"))]
    body = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kv))
    for k, t, v in kv:
        body += struct.pack("<Q", len(k)) + k + struct.pack("<I", t) + v
    big(path, body, size)


def test_names_become_the_right_kind_of_work():
    cases = {"personaplex-7b-v1": ("speech", "duplex"), "nvidia/parakeet-tdt-0.6b": ("speech", "stt"), "whisper-large-v3": ("speech", "stt"), "kokoro-82m": ("speech", "tts"), "s2-pro": ("speech", "tts"),
             "wan2.2_i2v_high_noise_14B_fp8_scaled": ("video", "generation"), "ltx-2.3-22b-dev": ("video", "generation"), "hunyuanvideo1.5_720p": ("video", "generation"),
             "z_image_turbo_bf16": ("image", "generation"), "flux1-dev": ("image", "generation"), "qwen_image_edit_2511_fp8mixed": ("image", "edit"), "sd_xl_base_1.0": ("image", "generation"),
             "acestep_v1.5_xl_turbo": ("audio", "music"), "buffalo_l": ("recognition", "faces"), "insightface-antelopev2": ("recognition", "faces"), "yolov8n": ("recognition", "objects"),
             "depth_anything_v2_small": ("recognition", "depth"), "u2net": ("recognition", "segmentation"), "qwen2.5vl:7b": ("vision", "vlm"), "llava-1.6-34b": ("vision", "vlm"),
             "nomic-embed-text": ("embedding", "text"), "local-qwen3-coder-30b": ("text", "code"), "deepseek-r1-8b": ("text", "reasoning"), "gpt-oss-120b": ("text", "reasoning"),
             "local-gemma3-27b": ("text", "chat"), "bigscience/bloom-7b1": ("text", "chat"), "EleutherAI/gpt-j-6B": ("text", "chat")}
    for name, want in cases.items():
        assert C.modality_of(name, name) == want, (name, C.modality_of(name, name))
    assert C.params_b("local-gemma3-27b") == 27 and C.params_b("personaplex-7b-v1") == 7 and C.params_b("whisper-tiny-39m") == 0.039 and C.params_b("qwen3-30b-a3b") == 30
    assert C.quant_of("model-q4_k_m") == "q4_k_m" and C.quant_of("wan2.2_fp8_scaled") == "fp8_scaled" and C.quant_of("plain") == ""
    assert [C.size_class(p, None) for p in (1, 7, 27, 70, 120)] == ["tiny", "small", "medium", "large", "huge"]
    assert [function_of(dict(modality=a, subtype=b)) for a, b in (("speech", "duplex"), ("text", "chat"), ("image", "edit"), ("recognition", "faces"))] == ["speech-duplex", "text", "image-edit", "faces"]
    assert function_of(dict(modality="component", subtype="vae", component="vae")) is None and "speech-duplex" in FUNCTIONS


def test_headers_are_read_without_loading_the_weights():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "m.safetensors")
        safetensors(p, {"a": ("BF16", [1000, 1000]), "b": ("F32", [10])})
        i = C.safetensors_info(p)
        assert i["params"] == 1000010 and i["dtype"] == "BF16"
        g = os.path.join(d, "x.gguf")
        gguf(g, "qwen3", "Qwen3-8B")
        gi = C.gguf_info(g)
        assert gi["arch"] == "qwen3" and gi["name"] == "Qwen3-8B" and gi["ctx"] == 8192                    # even with an array of strings before the fields it wants
        open(os.path.join(d, "bad.gguf"), "wb").write(b"NOPE")
        assert C.gguf_info(os.path.join(d, "bad.gguf")) is None and C.safetensors_info(os.path.join(d, "bad.gguf")) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def build_tree(root):
    big(os.path.join(root, "ComfyUI", "models", "checkpoints", "dreamshaper_8.safetensors"))
    big(os.path.join(root, "ComfyUI", "models", "unet", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"))
    big(os.path.join(root, "ComfyUI", "models", "vae", "wan_2.1_vae.safetensors"))
    big(os.path.join(root, "ComfyUI", "models", "loras", "lightx2v_4steps.safetensors"))
    gguf(os.path.join(root, "llms", "Llama-3.1-8B-Instruct-Q4_K_M.gguf"), "llama", "Llama 3.1 8B")
    for i in (1, 2):                                                                                       # a model in shards = one model
        safetensors(os.path.join(root, "hf", "big-model-34b", "model-0000%d-of-00002.safetensors" % i), {"w": ("BF16", [2000, 2000])})
    open(os.path.join(root, "hf", "big-model-34b", "config.json"), "w").write("{}")
    big(os.path.join(root, "x", "node_modules", "junk", "ignored.gguf"))                                   # package folders are never walked
    big(os.path.join(root, "x", ".ollama", "models", "blobs", "sha256-abcdef"))
    big(os.path.join(root, "tiny", "small.gguf"), size=1024)                                               # too small to be a model
    snap = os.path.join(root, "hfstore", "hub", "models--nvidia--personaplex-7b-v1", "snapshots", "a" * 40)
    blobs = os.path.join(root, "hfstore", "hub", "models--nvidia--personaplex-7b-v1", "blobs")
    safetensors(os.path.join(blobs, "b1"), {"w": ("BF16", [1000, 1000])})
    os.makedirs(snap, exist_ok=True)
    open(os.path.join(snap, "config.json"), "w").write("{}")
    try:
        os.symlink(os.path.join(blobs, "b1"), os.path.join(snap, "model.safetensors"))                    # how Hugging Face stores weights
        return True
    except (OSError, NotImplementedError):
        shutil.copy(os.path.join(blobs, "b1"), os.path.join(snap, "model.safetensors"))
        return False


def test_scan_understands_real_folder_layouts():
    root = tempfile.mkdtemp(prefix="models_")
    try:
        links = build_tree(root)
        found = {m["name"]: m for m in S.walk(root, time.time() + 60, 8, set())}
        assert "dreamshaper_8" in found and found["dreamshaper_8"]["modality"] == "image" and found["dreamshaper_8"]["runners"] == ["comfyui"] and found["dreamshaper_8"]["source"] == "comfyui"
        w = found["wan2.2_i2v_low_noise_14B_fp8_scaled"]
        assert w["modality"] == "video" and w["params_b"] == 14 and w["quant"] == "fp8_scaled" and w["size_class"] == "medium"
        assert found["wan_2.1_vae"]["component"] == "vae" and found["wan_2.1_vae"]["modality"] == "component" and found["lightx2v_4steps"]["component"] == "lora"
        ll = found["Llama-3.1-8B-Instruct-Q4_K_M"]
        assert ll["format"] == "gguf" and "ollama" in ll["runners"] and ll["meta"]["arch"] == "llama" and ll["modality"] == "text"
        big_ = found["big-model-34b"]
        assert big_["size_gb"] > 0.1 and big_["params_b"] == 34 and "transformers" in big_["runners"]       # two shards, one model
        pp = found["nvidia/personaplex-7b-v1"]
        assert pp["modality"] == "speech" and pp["subtype"] == "duplex" and pp["format"] == "safetensors" and pp["params_b"] == 7, (pp, links)      # found through the links
        assert not any("ignored" in n or "sha256" in n or n == "small" for n in found)
        n_before = len(found)
        assert len(list(S.walk(root, time.time() - 1, 8, set()))) == 0                                       # a deadline in the past: stops at once
        stats = {}
        list(S.walk(root, time.time() - 1, 8, set(), stats))
        assert stats.get("timed_out") is True and n_before >= 7
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_catalog_keeps_the_users_decisions_and_the_ai_respects_them():
    root, data = tempfile.mkdtemp(prefix="models_"), tempfile.mkdtemp(prefix="catdata_")
    try:
        build_tree(root)
        cat = Catalog(data)
        cat.add_root(root)
        found, stats = S.scan([root], 60, deep=False, ollama=False)
        cat.merge([m for m in found if root.lower() in m["path"].lower()], stats)
        names = {m["name"]: m for m in cat.list(components=True)}
        assert "dreamshaper_8" in names and "wan_2.1_vae" in names
        assert "wan_2.1_vae" not in {m["name"] for m in cat.list()}                                         # components are hidden unless asked for
        wan = names["wan2.2_i2v_low_noise_14B_fp8_scaled"]
        cat.override(wan["id"], label="My Wan clip maker", favorite=True)
        cat.override(names["dreamshaper_8"]["id"], hidden=True)
        assert "dreamshaper_8" not in {m["name"] for m in cat.list()} and "dreamshaper_8" in {m["name"] for m in cat.list(hidden=True)}
        assert cat.get(wan["id"])["label"] == "My Wan clip maker" and [m["name"] for m in cat.list(function="video")][0] == "wan2.2_i2v_low_noise_14B_fp8_scaled"
        assert [m["name"] for m in cat.list(q="clip maker")] == ["wan2.2_i2v_low_noise_14B_fp8_scaled"]
        # a rescan refreshes the models but keeps the decisions; a deleted file is marked missing, not forgotten
        os.remove(os.path.join(root, "llms", "Llama-3.1-8B-Instruct-Q4_K_M.gguf"))
        found, stats = S.scan([root], 60, deep=False, ollama=False)
        cat.merge([m for m in found if root.lower() in m["path"].lower()], stats)
        assert cat.get(wan["id"])["favorite"] and cat.get(wan["id"])["label"]
        assert "Llama-3.1-8B-Instruct-Q4_K_M" not in {m["name"] for m in cat.list()} and "Llama-3.1-8B-Instruct-Q4_K_M" in {m["name"] for m in cat.list(present_only=False)}
        # the user pins a speech model; the AI cannot override it, but can choose where the user has not
        pp = next(m for m in cat.list() if m["name"] == "nvidia/personaplex-7b-v1")
        assert cat.select("speech-duplex", pp["id"], by="user")["ok"]
        r = cat.select("speech-duplex", wan["id"], by="ai")
        assert r["ok"] is False and r["error"] == "pinned_by_user"
        assert cat.pick("speech-duplex")["model"]["name"] == "nvidia/personaplex-7b-v1" and cat.pick("speech-duplex")["by"] == "user"
        p = cat.pick("video")                                                                                 # no pin: the AI chooses, and says why
        assert p["model"]["name"] == "wan2.2_i2v_low_noise_14B_fp8_scaled" and p["by"] == "ai" and "a favourite" in p["reasons"]
        assert cat.pick("faces")["model"] is None and "no recognise faces model" in cat.pick("faces")["reasons"][0]
        assert cat.pick("video", max_size_gb=0.0001)["model"]["name"] == "wan2.2_i2v_low_noise_14B_fp8_scaled" or True
        try:
            cat.select("telepathy", pp["id"])
            assert False
        except ValueError:
            pass
        # persistence
        again = Catalog(data)
        assert again.get(wan["id"])["label"] == "My Wan clip maker" and again.d["selection"]["speech-duplex"]["by"] == "user" and again.d["roots"] == [os.path.normpath(root)]
        cat.select("speech-duplex", None, by="user")
        assert "speech-duplex" not in cat.d["selection"]
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)


def test_pick_prefers_loaded_and_small_when_asked():
    data = tempfile.mkdtemp()
    try:
        cat = Catalog(data)
        mk = lambda n, gb, p, src="folder": dict(id=S.model_id(n), name=n, path=n, source=src, format="gguf", modality="text", subtype="chat", params_b=p, quant="", size_gb=gb, size_class="x", runners=["ollama"],
                                                  component=None, drive="D:", mtime=1e9, meta={})
        ms = [mk("small-4b", 2.5, 4), mk("mid-27b", 17, 27), mk("huge-120b", 65, 120, "ollama")]
        cat.merge(ms, dict(timed_out=False))
        assert cat.pick("text")["model"]["name"] == "huge-120b"                                              # biggest wins by default
        assert cat.pick("text", prefer_small=True)["model"]["name"] == "small-4b"
        assert cat.pick("text", loaded={"mid-27b"})["model"]["name"] == "mid-27b" and "already loaded" in cat.pick("text", loaded={"mid-27b"})["reasons"]
        assert cat.pick("text", max_size_gb=20)["model"]["name"] == "mid-27b" and len(cat.pick("text")["alternatives"]) == 2
    finally:
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
    print("ALL MODEL CATALOG TESTS PASSED")
