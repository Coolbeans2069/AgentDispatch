"""speech to text on this computer: one audio or video file in, JSON out ({text, language, duration, segments}). Uses faster-whisper with a model that is ALREADY on disk (AD_MODEL_DIR, chosen from the
model catalog); it never downloads anything. Runs on the CPU unless AD_DEVICE=cuda, so it does not take VRAM from a render."""
import json, os, sys


def main(argv):
    if len(argv) < 1 or not os.path.isfile(argv[0]):
        print(json.dumps(dict(ok=False, error="give an audio or video file")))
        return 2
    model_dir = os.environ.get("AD_MODEL_DIR", "")
    if not model_dir or not os.path.isdir(model_dir):
        print(json.dumps(dict(ok=False, error="no local speech-to-text model was chosen: add a faster-whisper model on the Models page")))
        return 3
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        print(json.dumps(dict(ok=False, error="faster-whisper is not installed in this Python: %s" % e)))
        return 4
    device = os.environ.get("AD_DEVICE", "cpu")
    model = WhisperModel(model_dir, device=device, compute_type="float16" if device == "cuda" else "int8", local_files_only=True)
    segs, info = model.transcribe(argv[0], language=os.environ.get("AD_LANGUAGE") or None, vad_filter=True, beam_size=1)
    out, text = [], []
    for s in segs:
        out.append(dict(start=round(s.start, 2), end=round(s.end, 2), text=s.text.strip()))
        text.append(s.text.strip())
    print(json.dumps(dict(ok=True, text=" ".join(text).strip(), language=info.language, duration=round(info.duration, 1), segments=out[:400]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
