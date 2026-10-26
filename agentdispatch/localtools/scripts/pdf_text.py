"""extract text from a PDF file (PyMuPDF / pypdf). JSON out: pages, text excerpt, page count."""
import json, os, sys


def main(argv):
    path = argv[0] if argv else ""
    if not path or not os.path.isfile(path):
        print(json.dumps(dict(ok=False, error="give a PDF file path")))
        return 2
    if os.path.getsize(path) > 40 * 1048576:
        print(json.dumps(dict(ok=False, error="PDF larger than 40 MB")))
        return 3
    pages, engine = [], ""
    try:
        import fitz
        doc = fitz.open(path)
        for i, page in enumerate(doc):
            if i >= 40:
                break
            pages.append(dict(n=i + 1, text=(page.get_text("text") or "").strip()))
        engine = "pymupdf"
        meta = dict(page_count=doc.page_count, title=(doc.metadata or {}).get("title") or "")
        doc.close()
    except Exception:
        try:
            from pypdf import PdfReader
            r = PdfReader(path)
            for i, page in enumerate(r.pages[:40]):
                pages.append(dict(n=i + 1, text=(page.extract_text() or "").strip()))
            engine = "pypdf"
            meta = dict(page_count=len(r.pages), title=(r.metadata.title if r.metadata else "") or "")
        except Exception as e:
            print(json.dumps(dict(ok=False, error="could not read PDF: %s" % str(e)[:200])))
            return 4
    body = "\n\n".join("## page %d\n%s" % (p["n"], p["text"]) for p in pages if p["text"])
    print(json.dumps(dict(ok=True, engine=engine, page_count=meta["page_count"], title=str(meta.get("title") or "")[:200],
                          text=body[:14000], pages_returned=len(pages), note="%d characters extracted" % len(body)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
