"""image metadata and basic stats (Pillow / OpenCV optional). No writes."""
import json, os, sys


def main(argv):
    path = argv[0] if argv else ""
    if not path or not os.path.isfile(path):
        print(json.dumps(dict(ok=False, error="give an image file path")))
        return 2
    if os.path.getsize(path) > 60 * 1048576:
        print(json.dumps(dict(ok=False, error="image larger than 60 MB")))
        return 3
    out = dict(ok=True, path=os.path.basename(path), bytes=os.path.getsize(path))
    try:
        from PIL import Image, ExifTags
        im = Image.open(path)
        out.update(engine="pillow", format=im.format, mode=im.mode, size=list(im.size), width=im.size[0], height=im.size[1])
        exif = {}
        try:
            raw = im.getexif()
            for k, v in list(raw.items())[:40]:
                name = ExifTags.TAGS.get(k, str(k))
                if isinstance(v, (bytes, bytearray)):
                    continue
                exif[str(name)[:40]] = str(v)[:120]
        except Exception:
            pass
        if exif:
            out["exif"] = exif
        im.close()
    except Exception as e:
        out["pillow_error"] = str(e)[:120]
    try:
        import cv2
        im2 = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if im2 is not None:
            out["opencv_shape"] = list(im2.shape)
            out["opencv_dtype"] = str(im2.dtype)
    except Exception:
        pass
    if "width" not in out and "opencv_shape" not in out:
        print(json.dumps(dict(ok=False, error="could not read image (need Pillow or OpenCV)")))
        return 4
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
