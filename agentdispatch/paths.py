"""Host paths come from environment variables only (see .env.example); nothing here is tied to one machine.

AGENTDISPATCH_PROJECTS_ROOT  folder autonomous projects are created under (default: ~/agentdispatch-projects)
AGENTDISPATCH_WORK_DIR       throw-away copies of job targets (default: <tmp>/agentdispatch-work)
AGENTDISPATCH_SAFE_REPOS     comma-separated repos where auto-merge may be enabled (default: none)
AGENTDISPATCH_ALLOWED_ROOTS  comma-separated folders jobs and night-shift checks may target (default: the projects root)
OLLAMA_URL                   the Ollama server (default: http://127.0.0.1:11434; in Docker e.g. http://host.docker.internal:11434)
"""
import os, tempfile


def _norm(p):
    return os.path.expanduser(p).replace("\\", "/").rstrip("/") or "/"


def env_list(name, default=()):
    raw = os.environ.get(name, "")
    vals = [_norm(x) for x in raw.split(",") if x.strip()] if raw.strip() else list(default)
    return vals


PROJECTS_ROOT = _norm(os.environ.get("AGENTDISPATCH_PROJECTS_ROOT") or os.path.join("~", "agentdispatch-projects"))
WORK_DIR = _norm(os.environ.get("AGENTDISPATCH_WORK_DIR") or os.path.join(tempfile.gettempdir(), "agentdispatch-work"))
SAFE_REPOS = env_list("AGENTDISPATCH_SAFE_REPOS")
ALLOWED_ROOTS = env_list("AGENTDISPATCH_ALLOWED_ROOTS", [PROJECTS_ROOT])
OLLAMA_URL = (os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
