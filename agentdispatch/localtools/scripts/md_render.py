"""render Markdown to plain text or HTML (mistune/markdown). File in, text out."""
import json, os, sys


def main(argv):
    path = argv[0] if argv else ""
    fmt = (argv[1] if len(argv) > 1 else "text").lower()
    if not path or not os.path.isfile(path):
        print(json.dumps(dict(ok=False, error="give a Markdown file path")))
        return 2
    raw = open(path, encoding="utf-8", errors="replace").read()
    if len(raw) > 400000:
        print(json.dumps(dict(ok=False, error="file larger than 400 KB")))
        return 3
    html = ""
    engine = ""
    try:
        import mistune
        html = mistune.html(raw)
        engine = "mistune"
    except Exception:
        try:
            import markdown
            html = markdown.markdown(raw)
            engine = "markdown"
        except Exception as e:
            print(json.dumps(dict(ok=False, error="no markdown renderer: %s" % e)))
            return 4
    if fmt == "html":
        print(json.dumps(dict(ok=True, engine=engine, format="html", text=html[:14000]), ensure_ascii=False))
        return 0
    # strip tags lightly
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    print(json.dumps(dict(ok=True, engine=engine, format="text", text=text[:14000], note="%d chars" % len(text)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
