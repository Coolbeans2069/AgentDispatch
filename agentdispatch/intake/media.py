"""looking at what a person attached, AFTERWARDS (never live): speech is transcribed by a faster-whisper model that is already on disk, pictures are described by a vision model that is already in
Ollama, video is split into a few frames plus its sound track. Nothing tries to identify people. Everything runs on this computer; when a GPU is
busy with a render the vision step waits instead of competing for it."""
import base64, io, json, os, re, threading, time, urllib.request
from ..paths import OLLAMA_URL as _OLLAMA

OLLAMA = _OLLAMA
VISION_PROMPT = ("Describe this picture for someone who will build something from it: what is in it, the layout, any text, numbers, dimensions or labels you can read, materials, colours and anything "
                 "that looks like a requirement. Be concrete and brief (under 120 words). Do not guess who a person is.")


def pick_vision(app):
    """-> an Ollama vision model name: the person's pinned choice, else one that is already loaded, else the first one installed"""
    cat = app.catalog
    sel = cat.d["selection"].get("vision")
    names = []
    if sel:
        m = cat.get(sel["model_id"])
        if m and (m.get("path") or "").startswith("ollama:"):
            names.append(m["path"][7:])
    names += [m["path"][7:] for m in cat.list(function="vision", limit=20) if (m.get("path") or "").startswith("ollama:")]
    if not names:
        return None
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=2) as r:
            loaded = [x["name"] for x in json.loads(r.read()).get("models", [])]
        for n in names:
            if n in loaded:
                return n
    except Exception:
        pass
    return names[0]


def _jpeg_b64(path, max_side=1280):
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return base64.b64encode(open(path, "rb").read()).decode()


def describe(app, path, prompt=VISION_PROMPT, timeout=300):
    """-> (text | None, note). None + a note when it could not or should not run now."""
    model = pick_vision(app)
    if not model:
        return None, "no vision model is available (install one in Ollama, or choose one on the Models page)"
    why = app.availability.machine_busy() if getattr(app, "availability", None) else None
    if why:
        return None, "waiting: %s (a vision model would compete with it)" % why
    body = json.dumps(dict(model=model, stream=False, messages=[dict(role="user", content=prompt, images=[_jpeg_b64(path)])], options=dict(temperature=0.1, num_predict=400))).encode()
    try:
        req = urllib.request.Request(OLLAMA + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        return (d.get("message", {}).get("content", "").strip() or None), "described by %s" % model
    except Exception as e:
        return None, "the vision model %s did not answer: %s" % (model, str(e)[:120])


def transcribe(app, path, language=None):
    """-> (text | None, note)"""
    env = dict(AD_DEVICE="cpu")
    if language:
        env["AD_LANGUAGE"] = language
    r = app.localtools.run("transcribe", args=[path], timeout=1500, caller="intake", env=env)
    d = r.get("data") or {}
    if r.get("ok") and d.get("ok"):
        return d.get("text", ""), "transcribed locally (%s, %.0f s of audio)" % (d.get("language", "?"), d.get("duration", 0))
    return None, "; ".join(r.get("problems") or ["transcription failed"])[:200]


def video_parts(app, path, workdir, n_frames=4):
    """-> (frames [paths], audio path | None, seconds): a few evenly spaced frames and the sound track, made with ffmpeg"""
    os.makedirs(workdir, exist_ok=True)
    box = app.localtools
    dur = 0.0
    r = box.run("ffmpeg", args=["-hide_banner", "-i", path], caller="intake", timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", (r.get("stderr") or "") + (r.get("stdout") or ""))
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    frames = []
    for i in range(n_frames):
        t = dur * (i + 0.5) / n_frames if dur else i * 2.0
        out = os.path.join(workdir, "frame_%d.jpg" % (i + 1))
        rr = box.run("ffmpeg", args=["-y", "-loglevel", "error", "-ss", "%.2f" % t, "-i", path, "-frames:v", "1", "-q:v", "3", out], caller="intake", timeout=120)
        if rr.get("ok") and os.path.isfile(out):
            frames.append(out)
    audio = os.path.join(workdir, "audio.wav")
    ra = box.run("ffmpeg", args=["-y", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", audio], caller="intake", timeout=300)
    return frames, (audio if ra.get("ok") and os.path.isfile(audio) and os.path.getsize(audio) > 2000 else None), dur


def analyze(app, intake, sid, fid):
    """the whole look at one attachment, in a background thread. Writes the summary into the session and tells the chat what it found."""
    f = intake.file(sid, fid)
    intake.set_analysis(sid, fid, state="running", summary="")
    lines, details, waiting = [], {}, False
    try:
        if f["kind"] == "audio":
            text, note = transcribe(app, f["path"])
            lines.append(("Said: " + text) if text else "Could not transcribe it: " + note)
            details["transcript"] = text
        elif f["kind"] == "text":
            lines.append("Text file: " + open(f["path"], encoding="utf-8", errors="replace").read()[:1200])
        elif f["kind"] in ("image", "video"):
            paths, audio = [f["path"]], None
            if f["kind"] == "video":
                frames, audio, dur = video_parts(app, f["path"], os.path.join(os.path.dirname(f["path"]), "frames_" + fid))
                paths = frames
                details["seconds"] = round(dur, 1)
                lines.append("Video, about %.0f s, %d frame(s) taken." % (dur, len(frames)))
                if audio:
                    text, note = transcribe(app, audio)
                    if text:
                        lines.append("Said: " + text)
                        details["transcript"] = text
            descs = []
            for i, p in enumerate(paths[:4], 1):
                d, note = describe(app, p)
                if d:
                    descs.append(("Frame %d: " % i if f["kind"] == "video" else "") + d)
                elif "waiting" in note:
                    waiting = True
                    lines.append("Not looked at yet: " + note)
                    break
                else:
                    lines.append(note)
                    break
            lines += descs
            details["descriptions"] = descs
    except Exception as e:
        lines.append("The look failed: %s: %s" % (e.__class__.__name__, str(e)[:120]))
    summary = "\n".join(lines).strip() or "Nothing could be read from it."
    intake.set_analysis(sid, fid, state="waiting" if waiting else "done", summary=summary[:3000], details=details)
    if not waiting:
        intake.say(sid, "assistant", "I looked at %s.\n%s" % (f["name"], summary[:900]), auto=True)


def start(app, intake, sid, fid):
    t = threading.Thread(target=analyze, args=(app, intake, sid, fid), daemon=True)
    t.start()
    return t
