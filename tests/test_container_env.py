"""container settings come from environment variables: OLLAMA_URL reaches every Ollama caller, AGENTDISPATCH_HOST picks the listen address,
and AGENTDISPATCH_ALLOWED_HOSTS widens the DNS-rebinding guard only by the names it lists."""
import json, os, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def child(code, **env):
    e = dict(os.environ, **env)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip()


def test_ollama_url_default_and_override():
    clean = {k: v for k, v in os.environ.items() if k != "OLLAMA_URL"}
    r = subprocess.run([sys.executable, "-c", "from agentdispatch.paths import OLLAMA_URL; print(OLLAMA_URL)"], cwd=ROOT, env=clean, capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "http://127.0.0.1:11434", r.stdout + r.stderr
    out = child("import json, inspect\n"
                "from agentdispatch import router, orchestrator\n"
                "from agentdispatch.agency import watchdog, vram, gateway, conductor\n"
                "from agentdispatch.intake import media\n"
                "print(json.dumps([inspect.signature(orchestrator.ollama_llm).parameters['url'].default, watchdog.DEFAULTS['ollama'], conductor.DEFAULTS['ollama'],"
                " inspect.signature(vram.VramWatch.__init__).parameters['ollama'].default, inspect.signature(gateway.Gateway.__init__).parameters['ollama'].default, media.OLLAMA]))",
                OLLAMA_URL="http://ollama.test:11434/")
    vals = [v for v in json.loads(out) if v is not None]
    assert vals and all(v == "http://ollama.test:11434" for v in vals), vals
    print("ok test_ollama_url_default_and_override")


def test_bind_host_and_allowed_hosts():
    out = child("from agentdispatch import web; print(web.BIND_HOST, sorted(web.EXTRA_HOSTS))")
    assert out.startswith("127.0.0.1 "), out
    out = child("from agentdispatch import web; print(web.BIND_HOST, ','.join(sorted(web.EXTRA_HOSTS)))", AGENTDISPATCH_HOST="0.0.0.0", AGENTDISPATCH_ALLOWED_HOSTS=" AgentDispatch , engineroom ")
    assert out == "0.0.0.0 agentdispatch,engineroom", out
    print("ok test_bind_host_and_allowed_hosts")


def test_guard_accepts_listed_host_only():
    d = tempfile.mkdtemp(prefix="ad-ctr-")
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, AGENTDISPATCH_ALLOWED_HOSTS="agentdispatch", AGENTDISPATCH_DATA=d)
    p = subprocess.Popen([sys.executable, "-m", "agentdispatch", "--data", d, "serve", "--port", str(port), "--host", "127.0.0.1"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def get(host):
            req = urllib.request.Request("http://127.0.0.1:%d/api/stats" % port, headers={"Host": host})
            try:
                return urllib.request.urlopen(req, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code
        for _ in range(100):
            try:
                if get("127.0.0.1") == 200:
                    break
            except Exception:
                time.sleep(0.2)
        assert get("127.0.0.1") == 200
        assert get("agentdispatch:8870") == 200
        assert get("evil.example") == 403
    finally:
        p.terminate(); p.wait(10)
    print("ok test_guard_accepts_listed_host_only")


if __name__ == "__main__":
    test_ollama_url_default_and_override()
    test_bind_host_and_allowed_hosts()
    test_guard_accepts_listed_host_only()
    print("ALL CONTAINER ENV TESTS PASSED")
