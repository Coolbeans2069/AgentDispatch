"""what is this file / folder? From its path, name, format and (when cheap to read) its header: the kind of work it does (modality), its size class, how it is stored and what can run it.
Nothing here is specific to one machine; it works on names and formats, so models in any folder are understood."""
import json, os, re, struct

WEIGHT_EXT = {".gguf": "gguf", ".safetensors": "safetensors", ".ckpt": "ckpt", ".pt": "torch", ".pth": "torch", ".onnx": "onnx", ".bin": "bin", ".nemo": "nemo", ".engine": "tensorrt",
              ".mlmodel": "coreml", ".tflite": "tflite", ".pb": "tf"}
COMPONENT_DIRS = {"vae": "vae", "vae_approx": "vae", "clip": "text encoder", "clip_vision": "vision encoder", "text_encoders": "text encoder", "loras": "lora", "lora": "lora", "controlnet": "controlnet",
                  "upscale_models": "upscaler", "embeddings": "embedding", "hypernetworks": "hypernetwork", "style_models": "style", "gligen": "gligen", "vae_models": "vae", "ipadapter": "adapter"}
COMFY_DIRS = {"checkpoints", "unet", "diffusion_models", "loras", "vae", "clip", "text_encoders", "controlnet", "upscale_models", "clip_vision", "embeddings", "audio_encoders"}

# order matters: the first match wins. (modality, subtype, regex on the lower-cased name / path)
RULES = [
    ("speech", "duplex", r"personaplex|moshi\b|moshiko|moshika|full[-_ ]duplex|speech[-_ ]to[-_ ]speech"),
    ("speech", "stt", r"whisper|parakeet|canary|nemotron[-_ ]?speech|wav2vec|conformer|fastconformer|sensevoice|paraformer|\basr\b|speech[-_ ]?to[-_ ]?text|\bstt\b"),
    ("speech", "tts", r"kokoro|xtts|fish[-_ ]?audio|fishaudio|\bs2[-_ ]pro\b|f5[-_ ]?tts|orpheus|bark\b|piper|parler|chatterbox|styletts|vits|\btts\b|text[-_ ]?to[-_ ]?speech|voice[-_ ]?clone|csm[-_ ]?1b|dia[-_ ]1\.6"),
    ("audio", "music", r"ace[-_ ]?step|musicgen|stable[-_ ]audio|audioldm|riffusion|yue\b|songgen|diffrhythm"),
    ("video", "generation", r"\bwan[-_ ]?2|wan2\.|wan_|ltx[-_ ]?video|ltx-|ltxv|hunyuan[-_ ]?video|hunyuanvideo|cogvideo|mochi|animatediff|stable[-_ ]video|\bsvd\b|\bi2v\b|\bt2v\b|\bs2v\b|skyreels|framepack|video"),
    ("image", "edit", r"image[-_ ]?edit|qwen[-_ ]image[-_ ]edit|kontext|instruct[-_ ]?pix2pix"),
    ("image", "generation", r"v1-5-pruned|pruned-emaonly|emaonly|v2-1_|v1-4|flux|sd[-_ ]?xl|sd3|sd[-_]?1\.5|sd[-_]?2|stable[-_ ]diffusion|z[-_ ]image|qwen[-_ ]image|dreamshaper|juggernaut|realvis|illustrious|pony|hidream|chroma|lumina|playground|kolors|pixart|cascade"),
    ("recognition", "depth", r"depth[-_ ]anything|midas|zoedepth|\bdpt[-_]"),
    ("recognition", "segmentation", r"u2net|rembg|birefnet|isnet|modnet|matting|background[-_ ]remov"),
    ("recognition", "objects", r"\byolo|yolov|\bsam[-_]?(vit|2|hq)?\b|segment[-_ ]anything|detr\b|grounding[-_ ]?dino|owl[-_ ]?vit|\bdino(v2)?\b|clip[-_ ]vit|siglip|\bocr\b|florence"),
    ("vision", "vlm", r"qwen2?\.?5?[-_ ]?vl|-vl\b|[-_]vl[-_:]|llava|minicpm[-_ ]?v|moondream|pixtral|molmo|internvl|idefics|paligemma|smolvlm|vision|gemma3n?[-_ ]?(vision)|llama[-_ ]?3\.2[-_ ]?vision"),
    ("embedding", "text", r"embed|\bbge[-_]|\be5[-_]|minilm|\bgte[-_]|nomic|mxbai|snowflake[-_]arctic[-_]embed|reranker"),
    ("text", "code", r"coder|code[-_ ]?(llama|gemma|qwen|stral)|starcoder|codestral|deepseek[-_ ]?coder|devstral|codex"),
    ("text", "reasoning", r"\br1\b|-r1|reason|thinking|\bqwq\b|cascade|gpt[-_ ]?oss|\bo1\b|magistral|phi[-_ ]?4[-_ ]reason"),
    ("text", "chat", r"gpt[-_]?j|gpt[-_]?neo|gpt-?2\b|bloom|pythia|\bmpt[-_]|stablelm|rwkv|llama|qwen|gemma|mistral|mixtral|phi[-_ ]?\d|deepseek|nemotron|yi[-_ ]|command[-_ ]?r|granite|olmo|smollm|glm|kimi|minimax|internlm|solar|openchat|zephyr|vicuna|hermes|dolphin|orca"),
]
_PARAMS = re.compile(r"(?<![a-z0-9.])(\d{1,3}(?:\.\d{1,2})?)\s?([bm])(?![a-z0-9])(?:-?a?\d+b)?", re.I)
_QUANT = re.compile(r"(q\d(?:_[a-z0-9]+)*|iq\d(?:_[a-z0-9]+)*|fp8(?:_[a-z0-9]+)*|fp16|bf16|fp32|int4|int8|nf4|awq|gptq|mxfp4|e4m3fn?)", re.I)


def _tokens(*parts):
    return " ".join(str(p) for p in parts).lower().replace("\\", "/")


def modality_of(name, path="", fmt=""):
    """-> (modality, subtype) from the model's name and folder"""
    t = _tokens(name, path)
    for mod, sub, rx in RULES:
        if re.search(rx, t):
            return mod, sub
    base = os.path.basename(os.path.dirname(path.replace("\\", "/"))).lower()
    if base in ("checkpoints", "unet", "diffusion_models"):
        return "image", "generation"
    if fmt == "gguf":
        return "text", "chat"
    return "unknown", ""


def params_b(name):
    """parameter count in billions from names like '7b', '27B', 'a3b' (the active size of an MoE is ignored), '350m'"""
    best = None
    for m in _PARAMS.finditer(name.lower().replace("_", "-")):
        v = float(m.group(1)) / (1000.0 if m.group(2).lower() == "m" else 1.0)
        best = v if best is None else max(best, v)
    return best


def quant_of(name):
    m = _QUANT.search(name)
    return m.group(1).lower() if m else ""


def size_class(params, size_gb):
    p = params if params is not None else (size_gb * 1.8 if size_gb else None)
    if p is None:
        return "unknown"
    return "tiny" if p <= 2 else "small" if p <= 9 else "medium" if p <= 35 else "large" if p <= 80 else "huge"


# ---------------------------------------------------------------- cheap header readers
def safetensors_info(path, limit=8 * 1024 * 1024):
    """-> dict(params, dtypes) read from the first bytes of a .safetensors file (its JSON header), or None"""
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n <= 0 or n > limit:
                return None
            h = json.loads(f.read(n))
    except (OSError, ValueError, struct.error):
        return None
    params, dts = 0, {}
    for k, v in h.items():
        if k == "__metadata__" or not isinstance(v, dict):
            continue
        c = 1
        for d in v.get("shape", []):
            c *= int(d)
        params += c
        dts[v.get("dtype", "?")] = dts.get(v.get("dtype", "?"), 0) + c
    dom = max(dts, key=dts.get) if dts else ""
    return dict(params=params, dtype=dom, meta=(h.get("__metadata__") or {}))


def gguf_info(path, max_kv=400):
    """-> dict(arch, name, ctx, file_type) read from the GGUF header's key/value metadata, or None. Arrays and long strings are skipped, never loaded."""
    T = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            ver = struct.unpack("<I", f.read(4))[0]
            if ver < 2:
                return None
            f.read(8)
            nkv = struct.unpack("<Q", f.read(8))[0]
            out = {}

            def rd_str():
                n = struct.unpack("<Q", f.read(8))[0]
                if n > 4096:
                    f.seek(n, 1)
                    return None
                return f.read(n).decode("utf-8", "replace")

            def rd_val(t):
                if t in T:
                    sz = struct.calcsize(T[t])
                    return struct.unpack(T[t], f.read(sz))[0]
                if t == 8:
                    return rd_str()
                if t == 9:
                    it, cnt = struct.unpack("<IQ", f.read(12))
                    for _ in range(min(cnt, 1 << 20)):
                        if it == 8:
                            n = struct.unpack("<Q", f.read(8))[0]
                            f.seek(n, 1)
                        elif it in T:
                            f.seek(struct.calcsize(T[it]), 1)
                        else:
                            return None
                    return None
                raise ValueError(t)
            for _ in range(min(nkv, max_kv)):
                k = rd_str()
                t = struct.unpack("<I", f.read(4))[0]
                v = rd_val(t)
                if k in ("general.architecture", "general.name", "general.file_type", "general.size_label") or (k or "").endswith(".context_length"):
                    out[k] = v
            return dict(arch=out.get("general.architecture"), name=out.get("general.name"), file_type=out.get("general.file_type"), size_label=out.get("general.size_label"),
                        ctx=next((v for k, v in out.items() if k.endswith(".context_length")), None))
    except (OSError, ValueError, struct.error):
        return None


def runners_for(fmt, modality, subtype, in_comfy, is_repo, name):
    out = []
    if fmt == "gguf":
        out += ["ollama", "llama.cpp", "lm-studio"]
    if in_comfy:
        out.append("comfyui")
    if fmt == "nemo":
        out.append("nemo")
    if re.search(r"personaplex|moshi", name):
        out.append("personaplex")
    if is_repo and fmt in ("safetensors", "bin", "torch") and "comfyui" not in out:
        out.append("transformers")
    if fmt == "onnx":
        out.append("onnxruntime")
    return out or ["unknown"]
