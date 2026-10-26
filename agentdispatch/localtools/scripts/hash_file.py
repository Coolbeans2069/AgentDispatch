"""SHA-256 (and size) of a file inside an allowed folder. Verification helper."""
import hashlib, json, os, sys


def main(argv):
    path = argv[0] if argv else ""
    algo = (argv[1] if len(argv) > 1 else "sha256").lower()
    if algo not in ("sha256", "sha1", "md5"):
        print(json.dumps(dict(ok=False, error="algo must be sha256, sha1 or md5")))
        return 2
    if not path or not os.path.isfile(path):
        print(json.dumps(dict(ok=False, error="give a file path")))
        return 3
    if os.path.getsize(path) > 500 * 1048576:
        print(json.dumps(dict(ok=False, error="file larger than 500 MB")))
        return 4
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    print(json.dumps(dict(ok=True, algo=algo, digest=h.hexdigest(), bytes=os.path.getsize(path), name=os.path.basename(path))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
