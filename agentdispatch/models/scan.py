"""find models on this computer: known places first (fast), then a time-boxed walk of every fixed drive. No path is hard-coded to one machine: the places come from the environment, the user's home,
the drives that exist, and folders the user adds. Ollama's own models are read from its API (its blobs folder is never walked)."""
import hashlib, json, os, re, sys, time, urllib.request

from . import classify as C

MIN_BYTES = 64 * 1024 * 1024
MIN_ONNX = 4 * 1024 * 1024
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__", ".git", "$recycle.bin", "system volume information", "windows", "program files", "program files (x86)", "programdata", "winsxs",
             "recovery", "$windows.~bt", "temp", "tmp", "cache", "caches", "dist-packages", ".venv", "venv", "env", "blobs", "msocache", "perflogs", "docker", ".docker", "wsl", "steamapps",
             "unity", "library", ".gradle", ".m2", ".npm", ".cargo", "vcpkg", "conda", "miniconda3", "anaconda3", "intel", "nvidia corporation", "microsoft", "adobe"}
STORE_HINT = re.compile(r"model|checkpoint|llm|gguf|huggingface|^hf$|weights|comfy|unet|diffusion|lora|ollama|lmstudio|lm-studio|stable|whisper|nemo|ai[-_ ]?(project|models)?$", re.I)
SHARD = re.compile(r"^(?P<stem>.+?)[-_.](?P<i>\d{3,6})-of-(?P<n>\d{3,6})(?P<ext>\.[a-z0-9]+)$", re.I)


def model_id(path):
    return hashlib.sha1(os.path.normpath(path).lower().encode("utf-8", "replace")).hexdigest()[:10]


def fixed_drives():
    """every fixed drive (Windows letters, or / and common mount points elsewhere)"""
    if os.name == "nt":
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            out = []
            for i in range(26):
                if mask & (1 << i):
                    root = "%s:\\" % chr(65 + i)
                    if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:            # DRIVE_FIXED
                        out.append(root)
            return out
        except Exception:
            return [d for d in (c + ":\\" for c in "CDEF") if os.path.isdir(d)]
    return [p for p in ("/", "/mnt", "/media", "/data", "/opt") if os.path.isdir(p)]


def known_places(extra=()):
    """the folders where models usually are: environment variables, the user's home, ComfyUI folders found at a drive's top level, MediaStudio's own paths file, and anything the user added"""
    home = os.path.expanduser("~")
    env = [os.environ.get(k) for k in ("OLLAMA_MODELS", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "COMFYUI_MODELS", "LMSTUDIO_MODELS", "TORCH_HOME", "NEMO_CACHE_DIR")]
    places = [e for e in env if e]
    for rel in (".cache/huggingface/hub", ".cache/huggingface", ".lmstudio/models", ".cache/lm-studio/models", "models", ".cache/torch", ".cache/nemo", ".cache/whisper", "Documents/models", "ComfyUI/models"):
        places.append(os.path.join(home, *rel.split("/")))
    for d in fixed_drives():
        try:
            for e in os.scandir(d):
                if e.is_dir(follow_symlinks=False) and e.name.lower() not in SKIP_DIRS and STORE_HINT.search(e.name):
                    places.append(e.path)
        except OSError:
            pass
    for fp in [p for p in (os.environ.get("AGENTDISPATCH_FARM_PATHS"),) if p]:
        try:
            places.append(json.load(open(fp, encoding="utf-8")).get("base") or "")
        except Exception:
            pass
    places = list(extra) + places                                                      # the user's own folders first: they are never starved by the time budget
    seen, out = set(), []
    for p in places:
        n = os.path.normpath(p).lower() if p else ""
        if n and n not in seen and os.path.isdir(p):
            seen.add(n)
            out.append(os.path.normpath(p))
    return out


def _hf_name(path):
    m = re.search(r"models--([^\\/]+)--([^\\/]+)", path)
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def _entry(path, size, fmt, name, source, mtime, in_comfy=False, comp=None, is_repo=False, header=None):
    low = name.lower()
    modality, sub = C.modality_of(name, path, fmt)
    if comp:
        modality, sub = "component", comp
    meta = {}
    p = C.params_b(name)
    if header:
        meta = {k: v for k, v in header.items() if k in ("arch", "ctx", "dtype", "file_type", "size_label") and v}
        if header.get("params") and not p:
            p = header["params"] / 1e9
        if header.get("size_label") and not p:
            p = C.params_b(str(header["size_label"]))
    gb = round(size / 2**30, 2)
    return dict(id=model_id(path), name=name, path=path, source=source, format=fmt, modality=modality, subtype=sub, params_b=round(p, 2) if p else None, quant=C.quant_of(low) or meta.get("dtype", ""),
                size_gb=gb, size_class=C.size_class(p, gb), runners=C.runners_for(fmt, modality, sub, in_comfy, is_repo, low), component=comp, drive=os.path.splitdrive(path)[0] or "/",
                mtime=mtime, meta=meta)


def _group_dir(path, files):
    """the models in one directory: [(entry kwargs)]. A folder with a config + weights is one model (a Hugging Face style repo); shards are one model; otherwise each weight file is a model."""
    out = []
    parts = path.replace("\\", "/").lower().split("/")
    in_comfy = "comfyui" in "/".join(parts) or (len(parts) >= 2 and parts[-2] == "models" and parts[-1] in C.COMFY_DIRS) or parts[-1] in C.COMFY_DIRS
    comp = None
    for p in reversed(parts[-3:]):
        if p in C.COMPONENT_DIRS:
            comp = C.COMPONENT_DIRS[p]
            break
    weights = []
    for name, size, mt in files:
        ext = os.path.splitext(name)[1].lower()
        if ext in C.WEIGHT_EXT and size >= (MIN_ONNX if ext == ".onnx" else MIN_BYTES) and not name.lower().startswith("sha256-"):
            weights.append((name, size, mt, ext))
    if not weights:
        return out
    names = {n.lower() for n, _, _ in files}
    is_repo = bool({"config.json", "params.json", "model.safetensors.index.json", "pytorch_model.bin.index.json", "tokenizer.json", "adapter_config.json", "model_index.json"} & names) and not in_comfy
    if is_repo or any(w[3] == ".nemo" for w in weights) and False:
        total = sum(w[1] for w in weights)
        name = _hf_name(path) or os.path.basename(path)
        if re.fullmatch(r"[0-9a-f]{40}", name):                                  # a Hugging Face snapshot folder: use the repo name above it
            name = _hf_name(path) or os.path.basename(os.path.dirname(os.path.dirname(path)))
        dom = max(set(w[3] for w in weights), key=lambda e: sum(w[1] for w in weights if w[3] == e))
        header = C.safetensors_info(os.path.join(path, [w[0] for w in weights if w[3] == ".safetensors"][0])) if dom == ".safetensors" else None
        out.append(dict(path=path, size=total, fmt=C.WEIGHT_EXT[dom], name=name, mtime=max(w[2] for w in weights), is_repo=True, in_comfy=False, comp=None, header=header, source="hf-cache" if "models--" in path else "folder"))
        return out
    groups = {}
    for name, size, mt, ext in weights:
        m = SHARD.match(name)
        key = (m.group("stem") + m.group("ext")) if m else name
        g = groups.setdefault(key, dict(files=[], size=0, mt=0, ext=ext))
        g["files"].append(name)
        g["size"] += size
        g["mt"] = max(g["mt"], mt)
    for key, g in groups.items():
        first = sorted(g["files"])[0]
        full = os.path.join(path, first)
        header = None
        if g["ext"] == ".safetensors":
            header = C.safetensors_info(full)
        elif g["ext"] == ".gguf":
            header = C.gguf_info(full)
        out.append(dict(path=full, size=g["size"], fmt=C.WEIGHT_EXT[g["ext"]], name=os.path.splitext(key)[0], mtime=g["mt"], is_repo=False, in_comfy=in_comfy, comp=comp, header=header,
                        source="comfyui" if in_comfy else "lmstudio" if "lmstudio" in "/".join(parts) or "lm-studio" in "/".join(parts) else "folder"))
    return out


def walk(root, deadline, max_depth=7, seen_paths=None, stats=None):
    """yield model entries under root; stops at the deadline; never follows links; skips system and package folders"""
    stack = [(root, 0)]
    while stack:
        if time.time() > deadline:
            if stats is not None:
                stats["timed_out"] = True
            return
        path, depth = stack.pop()
        files, subs = [], []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            if e.is_file():                                          # a link to a file (Hugging Face snapshots): count the file it points at; links to folders are never followed
                                st = e.stat()
                                files.append((e.name, st.st_size, st.st_mtime))
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if e.name.lower() not in SKIP_DIRS and not e.name.startswith("$"):
                                subs.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            files.append((e.name, st.st_size, st.st_mtime))
                    except OSError:
                        continue
        except OSError:
            continue
        if stats is not None:
            stats["dirs"] = stats.get("dirs", 0) + 1
        for g in _group_dir(path, files):
            if seen_paths is not None:
                k = os.path.normpath(g["path"]).lower()
                if k in seen_paths:
                    continue
                seen_paths.add(k)
            yield _entry(g["path"], g["size"], g["fmt"], g["name"], g["source"], g["mtime"], g["in_comfy"], g["comp"], g["is_repo"], g["header"])
        if depth < max_depth:
            for s in subs:
                stack.append((s, depth + 1))


def ollama_models(url="http://127.0.0.1:11434"):
    """the models Ollama has, from its API (names, sizes, families); [] when it is not running"""
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=2) as r:
            tags = json.loads(r.read()).get("models", [])
    except Exception:
        return []
    out = []
    for m in tags:
        name, det = m.get("name", ""), m.get("details") or {}
        modality, sub = C.modality_of(name, "", "gguf")
        if "clip" in (det.get("families") or []) and modality == "text":
            modality, sub = "vision", "vlm"
        p = None
        try:
            ps = str(det.get("parameter_size", "")).lower()
            p = float(ps[:-1]) if ps.endswith("b") else float(ps[:-1]) / 1000 if ps.endswith("m") else None
        except ValueError:
            pass
        p = p or C.params_b(name)
        gb = round(m.get("size", 0) / 2**30, 2)
        out.append(dict(id=model_id("ollama:" + name), name=name, path="ollama:" + name, source="ollama", format="gguf", modality=modality, subtype=sub, params_b=round(p, 2) if p else None,
                        quant=str(det.get("quantization_level", "")).lower() or C.quant_of(name.lower()), size_gb=gb, size_class=C.size_class(p, gb), runners=["ollama"], component=None, drive="ollama",
                        mtime=0, meta=dict(arch=det.get("family"))))
    return out


def scan(extra_roots=(), budget_s=60.0, deep=True, progress=None, ollama=True):
    """-> (models, stats). Known places first (fast); then, with deep=True, a walk of every fixed drive until the time budget runs out (deep scans can be given a long budget)"""
    t0 = time.time()
    deadline = t0 + budget_s
    stats = dict(dirs=0, timed_out=False, places=0, roots=[])
    seen, found = set(), []
    if ollama:
        found += ollama_models()
    places = known_places(extra_roots)
    stats["places"] = len(places)
    for i, place in enumerate(places):
        if progress:
            progress("scanning " + place)
        for e in walk(place, deadline, 8, seen, stats):
            found.append(e)
        stats["roots"].append(place)
    if deep:
        for d in fixed_drives():
            if progress:
                progress("walking " + d)
            for e in walk(d, deadline, 6, seen, stats):
                found.append(e)
    stats["seconds"] = round(time.time() - t0, 1)
    stats["found"] = len(found)
    return found, stats
