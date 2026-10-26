"""what a model may ask this computer to run. A policy layer, not a hard sandbox: code from a model is checked BEFORE it runs (python calculations: an allow-list of modules and a deny-list of names;
Lean files: no IO, no process, no unsafe code), runs in a scratch folder with a timeout and an output cap, and never gets the owner's tokens in its environment. Tools that execute project code
(pytest, lake, custom tools) only run inside folders the owner trusts."""
import ast, os, re

CALC_IMPORTS = {"math", "cmath", "fractions", "decimal", "itertools", "functools", "operator", "statistics", "random", "json", "re", "string", "collections", "typing", "dataclasses", "numbers",
                "sympy", "mpmath", "numpy", "scipy", "z3", "flint", "gmpy2", "networkx", "heapq", "bisect", "datetime", "hashlib", "textwrap", "array", "enum", "time", "copy", "pprint",
                "pandas", "matplotlib", "sklearn", "statsmodels", "seaborn", "numba", "jax", "jaxlib", "xarray", "h5py", "pint", "uncertainties", "astropy", "skyfield", "symengine",
                "shapely", "pyproj", "geopandas", "jsonschema", "pydantic", "yaml", "toml", "orjson", "markdown", "mistune", "torch", "tiktoken", "PIL", "cv2", "skimage", "imageio",
                "trimesh", "soundfile", "librosa", "numpy.lib", "scipy.special", "scipy.optimize", "scipy.integrate", "scipy.linalg", "scipy.stats", "scipy.signal", "scipy.ndimage",
                "sklearn.metrics", "sklearn.model_selection", "sklearn.preprocessing", "sklearn.linear_model", "sklearn.cluster", "astropy.units", "astropy.constants", "astropy.coordinates",
                "matplotlib.pyplot", "mpl_toolkits", "polars", "duckdb", "cvxpy", "pulp", "base64", "struct", "unicodedata", "difflib", "csv", "io"}
CALC_BAD_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals", "vars", "getattr", "setattr", "delattr", "memoryview", "exit", "quit", "help"}
CALC_BAD_ATTRS = {"save", "savez", "savez_compressed", "savetxt", "load", "loadtxt", "genfromtxt", "fromfile", "tofile", "memmap", "fromregex", "system", "popen", "remove", "rmdir", "unlink", "rename",
                  "write_text", "write_bytes", "read_text", "read_bytes", "mkdir", "makedirs", "kill", "fork", "spawn", "execv", "startfile", "urlopen", "urlretrieve", "socket", "lambdify_source",
                  "read_csv", "read_excel", "read_parquet", "read_json", "read_sql", "to_csv", "to_excel", "to_parquet", "to_sql", "to_pickle", "read_pickle", "download",
                  "imread", "imwrite", "imsave", "VideoCapture"}
MAX_CODE_CHARS = 20000
LEAN_BAD = re.compile(r"\bIO\.|\bIO\b\s*\(|System\.|\bunsafe\b|@\[\s*(extern|implemented_by)|\brun_cmd\b|\brun_elab\b|\brun_meta\b|\binitialize\b|\bbuiltin_initialize\b|\bimport\s+Lake\b|\bstd\.|\bunsafeIO\b|\bunsafeBaseIO\b", re.I)
GIT_OK = {"status", "log", "show", "diff", "rev-parse", "ls-files", "cat-file", "describe", "rev-list", "ls-tree", "branch", "tag", "archive", "blame", "shortlog", "show-ref", "merge-base", "name-rev"}
GIT_BAD_FLAGS = ("-c", "--exec", "--upload-pack", "--receive-pack", "--output", "-o", "--ext-diff", "--textconv", "--open-files-in-pager")
SECRET_ENV = re.compile(r"token|secret|key|password|passwd|credential|auth", re.I)


MAX_CALC_DEPTH = 400                                                                # syntax-tree depth; real calculations stay far below this


def _depth(tree):
    """the deepest nesting of a syntax tree, counted without recursion"""
    deepest, todo = 0, [(tree, 1)]
    while todo:
        node, d = todo.pop()
        deepest = max(deepest, d)
        todo.extend((c, d + 1) for c in ast.iter_child_nodes(node))
    return deepest


def check_calc(code):
    """-> [] when the python calculation may run, else the reasons it may not"""
    if not isinstance(code, str) or not code.strip():
        return ["no code given"]
    if len(code) > MAX_CODE_CHARS:
        return ["the code is longer than %d characters" % MAX_CODE_CHARS]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return ["syntax error: %s (line %s)" % (e.msg, e.lineno)]
    except (RecursionError, MemoryError, ValueError):
        return ["the code is nested too deeply to check"]
    if _depth(tree) > MAX_CALC_DEPTH:                                               # Python 3.12+ parses `1+1+...+1` thousands deep without a RecursionError: refuse it here, the same on every version
        return ["the code is nested too deeply to check"]
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] not in CALC_IMPORTS:
                    bad.append("import %s is not allowed (allowed: %s)" % (a.name, ", ".join(sorted(CALC_IMPORTS)[:12]) + ", ..."))
        elif isinstance(n, ast.ImportFrom):
            if n.level or (n.module or "").split(".")[0] not in CALC_IMPORTS:
                bad.append("from %s import ... is not allowed" % (n.module or "."))
        elif isinstance(n, ast.Name) and (n.id in CALC_BAD_NAMES or n.id.startswith("__")):                   # __builtins__ gives back open / __import__ by another name
            bad.append("the name %s is not allowed" % n.id)
        elif isinstance(n, ast.Attribute):
            if n.attr.startswith("__") or n.attr in CALC_BAD_ATTRS:
                bad.append("the attribute .%s is not allowed" % n.attr)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bad.append("global / nonlocal is not allowed")
    return sorted(set(bad))[:8]


def check_lean_text(text):
    """-> [] when Lean source is free of IO / process / unsafe code"""
    hits = sorted({m.group(0).strip() for m in LEAN_BAD.finditer(text or "")})
    return ["Lean source uses %s, which is not allowed for code from a model" % h for h in hits[:6]]


def under(path, roots):
    """True when path is inside one of the roots (both resolved, case-insensitive on Windows)"""
    p = os.path.normcase(os.path.realpath(path))
    for r in roots or []:
        rr = os.path.normcase(os.path.realpath(r))
        if p == rr or p.startswith(rr + os.sep):
            return True
    return False


def clean_env(extra=None, gpu=None):
    """the environment a tool runs in: no secrets, UTF-8, quiet, optionally pinned to GPUs"""
    keep = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    keep.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", NO_COLOR="1", PYTHONDONTWRITEBYTECODE="1")
    if gpu is not None:
        keep["CUDA_VISIBLE_DEVICES"] = str(gpu)
    keep.update(extra or {})
    return keep


def git_args_ok(args):
    """-> (ok, why) for a read-only git command"""
    if not args or args[0] not in GIT_OK:
        return False, "git %s is not allowed (read-only commands only: %s)" % (args[0] if args else "", ", ".join(sorted(GIT_OK)))
    for a in args[1:]:
        if a in GIT_BAD_FLAGS or a.startswith(("--exec", "--upload-pack", "--output=", "-c=")):
            return False, "git flag %s is not allowed" % a
    if args[0] in ("branch", "tag") and any(not a.startswith("-") for a in args[1:]) and not any(a in ("--list", "-l", "--contains", "--points-at") for a in args[1:]):
        return False, "git %s may only list" % args[0]
    return True, ""
