"""fetch a public HTTPS page and return plain text (title, links, body excerpt). No cookies, no POST, no file URLs."""
import json, re, sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.links, self.title, self._skip, self._intitle = [], [], "", False, False

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg", "template"):
            self._skip = True
        if t == "title":
            self._intitle = True
        if t == "a":
            href = dict(attrs).get("href") or ""
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self.links.append(href)
        if t in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg", "template"):
            self._skip = False
        if t == "title":
            self._intitle = False

    def handle_data(self, data):
        if self._skip:
            return
        s = re.sub(r"\s+", " ", data or "").strip()
        if not s:
            return
        if self._intitle:
            self.title += ((" " if self.title else "") + s)
        else:
            self.parts.append(s + " ")


def _ok_url(u):
    try:
        p = urlparse(u)
    except Exception:
        return False
    if p.scheme not in ("https", "http") or not p.netloc:
        return False
    host = (p.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return False
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)", host):
        return False
    return True


def main(argv):
    url = (argv[0] if argv else "").strip()
    if not url or not _ok_url(url):
        print(json.dumps(dict(ok=False, error="give a public http(s) URL (no localhost / private IPs)")))
        return 2
    try:
        import httpx
    except ImportError:
        try:
            import urllib.request as ur
            req = ur.Request(url, headers={"User-Agent": "AgentDispatch-fetch/1.0", "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.5"})
            with ur.urlopen(req, timeout=25) as r:
                raw = r.read(2_000_000)
                ctype = r.headers.get("Content-Type", "")
                final = r.geturl()
                status = getattr(r, "status", 200)
        except Exception as e:
            print(json.dumps(dict(ok=False, error="fetch failed: %s" % str(e)[:200])))
            return 3
    else:
        try:
            with httpx.Client(follow_redirects=True, timeout=25.0, headers={"User-Agent": "AgentDispatch-fetch/1.0", "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.5"}) as c:
                r = c.get(url)
                raw = r.content[:2_000_000]
                ctype = r.headers.get("content-type", "")
                final = str(r.url)
                status = r.status_code
        except Exception as e:
            print(json.dumps(dict(ok=False, error="fetch failed: %s" % str(e)[:200])))
            return 3
    if not _ok_url(final):
        print(json.dumps(dict(ok=False, error="redirect landed on a blocked host")))
        return 4
    text = raw.decode("utf-8", "replace")
    out = dict(ok=True, url=final, status=status, content_type=ctype.split(";")[0].strip()[:80], bytes=len(raw))
    if "json" in ctype or (text[:1] in "{["):
        out["text"] = text[:12000]
        out["kind"] = "json_or_text"
    elif "html" in ctype or "<html" in text[:500].lower() or "<!doctype" in text[:200].lower():
        p = _Strip()
        try:
            p.feed(text)
            p.close()
        except Exception:
            pass
        body = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", "".join(p.parts))).strip()
        links = []
        for href in p.links[:40]:
            links.append(urljoin(final, href)[:300])
        out.update(kind="html", title=(p.title or "")[:200], text=body[:12000], links=links, note="%d characters of visible text" % len(body))
    else:
        out.update(kind="text", text=text[:12000])
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
