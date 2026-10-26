"""audio/video stream probe via soundfile / imageio. Duration, channels, rate."""
import json, os, sys


def main(argv):
    path = argv[0] if argv else ""
    if not path or not os.path.isfile(path):
        print(json.dumps(dict(ok=False, error="give an audio or video file")))
        return 2
    out = dict(ok=True, name=os.path.basename(path), bytes=os.path.getsize(path))
    try:
        import soundfile as sf
        info = sf.info(path)
        out.update(engine="soundfile", samplerate=info.samplerate, channels=info.channels, duration_s=round(info.duration, 3), format=info.format, subtype=info.subtype)
    except Exception as e:
        out["soundfile_error"] = str(e)[:120]
    try:
        import imageio.v3 as iio
        meta = iio.immeta(path, plugin="pyav") if False else None
    except Exception:
        meta = None
    if "duration_s" not in out:
        try:
            from pydub import AudioSegment
            a = AudioSegment.from_file(path)
            out.update(engine=out.get("engine") or "pydub", duration_s=round(len(a) / 1000.0, 3), channels=a.channels, samplerate=a.frame_rate, sample_width=a.sample_width)
        except Exception as e:
            if "duration_s" not in out:
                print(json.dumps(dict(ok=False, error="could not probe audio: %s" % str(e)[:200])))
                return 3
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
