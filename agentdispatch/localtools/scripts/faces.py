"""find faces in pictures and, if a gallery of known people is given, say who they look like: JSON out. Uses insightface (buffalo_l, already in ~/.insightface) on the CPU so it never takes VRAM from a render.
usage: faces.py IMAGE [IMAGE ...]   (AD_GALLERY = a folder with one sub-folder per known person holding photos of them; AD_SIM_MIN = the similarity needed to name someone, default 0.40)
A face match is a SIMILARITY SCORE, not proof of identity: it is reported with the score so a person can judge."""
import glob, json, os, sys


def main(argv):
    imgs = [a for a in argv if os.path.isfile(a)]
    if not imgs:
        print(json.dumps(dict(ok=False, error="give one or more image files")))
        return 2
    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
    except ImportError as e:
        print(json.dumps(dict(ok=False, error="insightface / opencv are not installed in this Python: %s" % e)))
        return 4
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))

    def faces_of(path):
        im = cv2.imread(path)
        return [] if im is None else app.get(im)

    gallery = {}
    gdir = os.environ.get("AD_GALLERY", "")
    if gdir and os.path.isdir(gdir):
        for person in sorted(os.listdir(gdir)):
            vecs = []
            for p in glob.glob(os.path.join(gdir, person, "*")):
                if p.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
                    fs = faces_of(p)
                    if fs:
                        vecs.append(max(fs, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])).normed_embedding)
            if vecs:
                gallery[person] = np.mean(vecs, axis=0)
    sim_min = float(os.environ.get("AD_SIM_MIN", "0.40"))
    res = []
    for p in imgs[:12]:
        out = []
        for f in faces_of(p):
            d = dict(box=[int(x) for x in f.bbox], score=round(float(f.det_score), 3), age=int(f.age) if getattr(f, "age", None) is not None else None, gender=("female" if getattr(f, "gender", 1) == 0 else "male"))
            if gallery:
                best = max(((n, float(np.dot(f.normed_embedding, v) / (np.linalg.norm(v) + 1e-9))) for n, v in gallery.items()), key=lambda t: t[1])
                d["nearest"], d["similarity"] = best[0], round(best[1], 3)
                d["looks_like"] = best[0] if best[1] >= sim_min else None
            out.append(d)
        res.append(dict(file=os.path.basename(p), faces=out, count=len(out)))
    print(json.dumps(dict(ok=True, results=res, known_people=sorted(gallery)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
