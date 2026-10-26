"""the owner can re-point a job's dependencies (a job held by a cancelled probe copy can be freed) - jobs.set_dependencies and the owner-only POST /api/jobs/depends"""
import os, shutil, sys, tempfile, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_web import call
from agentdispatch.app import App
from agentdispatch.web import Server, make_handler, new_token


def test_set_dependencies_and_owner_only_endpoint():
    d = tempfile.mkdtemp(prefix="deps_")
    try:
        app = App(d)
        J = app.jobs
        a = J.create("dev", "a", "a goal", "general", "", None, None, 5)
        b = J.create("dev", "b", "b goal", "general", "", None, None, 5)
        c = J.create("dev", "c", "c goal", "general", "", None, None, 5)
        w = J.create("dev", "w", "w goal", "general", "", None, None, 5, dict(depends_on_jobs=[a["id"], b["id"]]))
        assert J.set_dependencies(w["id"], "dev", replace={b["id"]: c["id"]}) == [a["id"], c["id"]]
        assert J.meta(w["id"])["depends_on_jobs"] == [a["id"], c["id"]]
        try:
            J.set_dependencies(w["id"], "dev", drop=[b["id"]])
            assert False
        except Exception as e:
            assert "does not depend on" in str(e)
        try:
            J.set_dependencies(w["id"], "dev", replace={a["id"]: "J-999999"})
            assert False
        except Exception:
            pass
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        agent, owner = new_token(d, "codex-cli"), new_token(d, "dev", "owner")
        st, r = call(port, "POST", "/api/jobs/depends", dict(job_id=w["id"], drop=[a["id"]]), agent)
        assert st == 403 and r["error"] == "owner_only"
        st, r = call(port, "POST", "/api/jobs/depends", dict(job_id=w["id"], drop=[a["id"]]), owner)
        assert st == 200 and r["depends_on_jobs"] == [c["id"]], r
        st, r = call(port, "POST", "/api/jobs/depends", dict(job_id=w["id"], drop=["J-424242"]), owner)
        assert st == 400 and r["error"] == "bad_dependency", r
        srv.shutdown()
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_set_dependencies_and_owner_only_endpoint()
    print("ok")
