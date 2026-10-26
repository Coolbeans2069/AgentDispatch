"""render a public HTTPS page with Playwright (Chromium) and return visible text. Headless, no downloads, no private hosts."""
import json, re, sys
from urllib.parse import urlparse


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
        print(json.dumps(dict(ok=False, error="give a public http(s) URL")))
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(json.dumps(dict(ok=False, error="playwright is not installed: %s" % e)))
        return 3
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(25000)
            page.goto(url, wait_until="domcontentloaded")
            final = page.url
            if not _ok_url(final):
                browser.close()
                print(json.dumps(dict(ok=False, error="redirect landed on a blocked host")))
                return 4
            title = page.title() or ""
            text = page.inner_text("body") if page.query_selector("body") else ""
            text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
            links = page.eval_on_selector_all("a[href]", "els => els.slice(0,40).map(e => e.href)") or []
            browser.close()
    except Exception as e:
        print(json.dumps(dict(ok=False, error="browser fetch failed: %s" % str(e)[:240])))
        return 5
    print(json.dumps(dict(ok=True, kind="rendered", url=final, title=title[:200], text=text[:12000], links=[str(x)[:300] for x in links], note="%d characters of visible text" % len(text)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
