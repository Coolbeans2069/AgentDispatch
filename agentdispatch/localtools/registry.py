"""the tools this computer has: what each one is, where it is, which version, and how to get it when it is missing. Detection is by looking (PATH, the usual folders, Python environments found on the
fixed drives, importable libraries), never by a fixed path, so another user's machine gets its own list. Tools a model engineered (data/tools/custom/<name>/manifest.json) join the list by themselves.
`lobe` is where the tool sits in the brain picture."""
import glob, json, os, re, shutil, subprocess, sys, time

from ..nightshift import kill_tree

NOWIN = 0x08000000 if os.name == "nt" else 0

# kind: runtime | calc | lib | formal | test | lint | vcs | media | gpu | sandbox | browse | science | verify | docs | geo | ml | custom.
# run: how a model may use it (calc | lib | exe | script | pyargs | pymodule | read_file | local_llm | python | custom).
# pip / recipe: how it is installed when missing (always owner-approved unless the owner allowed it)
SPECS = [
    # ---- runtime / core ----
    dict(id="python", name="Python", kind="runtime", lobe="parietal", color="#ffd34d", desc="a Python interpreter (the environment used for calculations)", run="python", exe=["python", "python3", "py"]),
    dict(id="calc", name="Python calculator", kind="calc", lobe="parietal", color="#5dff9a", desc="run a short exact / numeric / scientific calculation (sympy, numpy, scipy, pandas, pint, astropy, …) and print the answer; no files, no network", run="calc", needs=["python"]),
    dict(id="read_file", name="Read a file", kind="files", lobe="temporal", color="#9fd8ff", desc="read a text file inside a project or work folder (up to 12,000 characters; give an offset to read on)", run="read_file"),
    dict(id="local_llm", name="Local model (Ollama)", kind="model", lobe="cerebellum", color="#b57bff", desc="ask a model on this computer (free) to draft, summarise, extract or generate: always check what it returns", run="local_llm"),
    dict(id="hash_file", name="File hash (SHA-256)", kind="verify", lobe="cerebellum", color="#19f0c8", desc="SHA-256 / SHA-1 / MD5 digest of a file in an allowed folder (verification)", run="script", script="hash_file.py", lib="hashlib"),
    # ---- symbolic / numeric science (import inside calc) ----
    dict(id="sympy", name="SymPy", kind="lib", lobe="parietal", color="#7bf0c4", desc="symbolic algebra, calculus, exact numbers", run="lib", lib="sympy", pip="sympy"),
    dict(id="mpmath", name="mpmath", kind="lib", lobe="parietal", color="#7bf0c4", desc="arbitrary-precision and interval arithmetic", run="lib", lib="mpmath", pip="mpmath"),
    dict(id="numpy", name="NumPy", kind="lib", lobe="parietal", color="#7bf0c4", desc="fast arrays and linear algebra", run="lib", lib="numpy", pip="numpy"),
    dict(id="scipy", name="SciPy", kind="lib", lobe="parietal", color="#7bf0c4", desc="numerical integration, optimisation, special functions", run="lib", lib="scipy", pip="scipy"),
    dict(id="pandas", name="pandas", kind="lib", lobe="parietal", color="#7bf0c4", desc="tabular data frames (in-memory; use csv_stats / xlsx_probe to read files)", run="lib", lib="pandas", pip="pandas"),
    dict(id="matplotlib", name="Matplotlib", kind="lib", lobe="occipital", color="#4da3ff", desc="plots (in calc: build figures in memory; file save is blocked)", run="lib", lib="matplotlib", pip="matplotlib"),
    dict(id="sklearn", name="scikit-learn", kind="lib", lobe="parietal", color="#7bf0c4", desc="classical machine learning: fit, predict, metrics on in-memory arrays", run="lib", lib="sklearn", pip="scikit-learn"),
    dict(id="statsmodels", name="statsmodels", kind="lib", lobe="parietal", color="#7bf0c4", desc="statistical models, tests, time-series", run="lib", lib="statsmodels", pip="statsmodels"),
    dict(id="seaborn", name="seaborn", kind="lib", lobe="occipital", color="#4da3ff", desc="statistical graphics on top of matplotlib", run="lib", lib="seaborn", pip="seaborn"),
    dict(id="numba", name="Numba", kind="lib", lobe="parietal", color="#7bf0c4", desc="JIT-compile numeric Python for speed", run="lib", lib="numba", pip="numba"),
    dict(id="jax", name="JAX", kind="lib", lobe="parietal", color="#7bf0c4", desc="composable numpy with autodiff and accelerators", run="lib", lib="jax", pip="jax"),
    dict(id="xarray", name="xarray", kind="lib", lobe="parietal", color="#7bf0c4", desc="labelled multi-dimensional arrays (netCDF-style)", run="lib", lib="xarray", pip="xarray"),
    dict(id="h5py", name="h5py", kind="lib", lobe="parietal", color="#7bf0c4", desc="HDF5 arrays (file IO blocked inside calc; use for in-memory patterns)", run="lib", lib="h5py", pip="h5py"),
    dict(id="pint", name="Pint", kind="lib", lobe="parietal", color="#7bf0c4", desc="physical units and conversions (also tool unit_convert)", run="lib", lib="pint", pip="pint"),
    dict(id="uncertainties", name="uncertainties", kind="lib", lobe="parietal", color="#7bf0c4", desc="numbers with error bars that propagate", run="lib", lib="uncertainties", pip="uncertainties"),
    dict(id="astropy", name="Astropy", kind="lib", lobe="parietal", color="#7bf0c4", desc="astronomy: units, coordinates, FITS helpers, constants", run="lib", lib="astropy", pip="astropy"),
    dict(id="skyfield", name="Skyfield", kind="lib", lobe="parietal", color="#7bf0c4", desc="planet and satellite positions from ephemerides", run="lib", lib="skyfield", pip="skyfield"),
    dict(id="symengine", name="SymEngine", kind="lib", lobe="parietal", color="#7bf0c4", desc="fast C++ symbolic backend usable from Python", run="lib", lib="symengine", pip="symengine"),
    dict(id="z3", name="Z3 solver", kind="lib", lobe="frontal", color="#ff9a1a", desc="an SMT solver: satisfiability and proofs for finite problems", run="lib", lib="z3", pip="z3-solver"),
    dict(id="flint", name="python-flint", kind="lib", lobe="parietal", color="#7bf0c4", desc="fast exact and interval (ball) arithmetic", run="lib", lib="flint", pip="python-flint"),
    dict(id="gmpy2", name="gmpy2", kind="lib", lobe="parietal", color="#7bf0c4", desc="fast big-number arithmetic", run="lib", lib="gmpy2", pip="gmpy2"),
    dict(id="networkx", name="NetworkX", kind="lib", lobe="parietal", color="#7bf0c4", desc="graphs: connectivity, paths, centrality", run="lib", lib="networkx", pip="networkx"),
    dict(id="shapely", name="Shapely", kind="geo", lobe="parietal", color="#5dff9a", desc="planar computational geometry (points, polygons, buffers)", run="lib", lib="shapely", pip="shapely"),
    dict(id="pyproj", name="pyproj", kind="geo", lobe="parietal", color="#5dff9a", desc="cartographic projections and CRS transforms", run="lib", lib="pyproj", pip="pyproj"),
    dict(id="geopandas", name="GeoPandas", kind="geo", lobe="parietal", color="#5dff9a", desc="geospatial tables (in-memory; file IO blocked in calc)", run="lib", lib="geopandas", pip="geopandas"),
    dict(id="jsonschema", name="jsonschema", kind="verify", lobe="cerebellum", color="#19f0c8", desc="validate data against a JSON Schema (also tool json_check)", run="lib", lib="jsonschema", pip="jsonschema"),
    dict(id="pydantic", name="Pydantic", kind="verify", lobe="cerebellum", color="#19f0c8", desc="typed data validation models", run="lib", lib="pydantic", pip="pydantic"),
    dict(id="yaml", name="PyYAML", kind="lib", lobe="temporal", color="#9fd8ff", desc="parse / dump YAML in memory", run="lib", lib="yaml", pip="pyyaml"),
    dict(id="toml", name="toml", kind="lib", lobe="temporal", color="#9fd8ff", desc="parse TOML in memory", run="lib", lib="toml", pip="toml"),
    dict(id="orjson", name="orjson", kind="lib", lobe="temporal", color="#9fd8ff", desc="fast JSON encode/decode", run="lib", lib="orjson", pip="orjson"),
    dict(id="markdown", name="markdown", kind="docs", lobe="temporal", color="#9fd8ff", desc="Markdown to HTML (also tool md_render)", run="lib", lib="markdown", pip="markdown"),
    dict(id="mistune", name="mistune", kind="docs", lobe="temporal", color="#9fd8ff", desc="fast Markdown renderer", run="lib", lib="mistune", pip="mistune"),
    # ---- ML / vision / audio libs (import or dedicated scripts) ----
    dict(id="torch", name="PyTorch", kind="ml", lobe="cerebellum", color="#b57bff", desc="tensors and deep learning (file load blocked inside calc)", run="lib", lib="torch", pip="torch"),
    dict(id="transformers", name="Hugging Face Transformers", kind="ml", lobe="cerebellum", color="#b57bff", desc="pretrained NLP / vision / audio models API", run="lib", lib="transformers", pip="transformers"),
    dict(id="sentence_transformers", name="Sentence-Transformers", kind="ml", lobe="cerebellum", color="#b57bff", desc="embedding models for semantic similarity", run="lib", lib="sentence_transformers", pip="sentence-transformers"),
    dict(id="tiktoken", name="tiktoken", kind="ml", lobe="temporal", color="#b57bff", desc="tokenise text the way OpenAI models count tokens", run="lib", lib="tiktoken", pip="tiktoken"),
    dict(id="nltk", name="NLTK", kind="ml", lobe="broca", color="#ffd34d", desc="classical NLP tokenisers and corpora helpers", run="lib", lib="nltk", pip="nltk"),
    dict(id="spacy", name="spaCy", kind="ml", lobe="broca", color="#ffd34d", desc="industrial NLP pipelines", run="lib", lib="spacy", pip="spacy"),
    dict(id="PIL", name="Pillow", kind="vision", lobe="occipital", color="#5dff9a", desc="image load/create in memory (also tool image_info)", run="lib", lib="PIL", pip="pillow"),
    dict(id="cv2", name="OpenCV", kind="vision", lobe="occipital", color="#5dff9a", desc="computer vision arrays and filters", run="lib", lib="cv2", pip="opencv-python-headless"),
    dict(id="skimage", name="scikit-image", kind="vision", lobe="occipital", color="#5dff9a", desc="image processing algorithms", run="lib", lib="skimage", pip="scikit-image"),
    dict(id="imageio", name="imageio", kind="media", lobe="occipital", color="#ff4d9d", desc="read image / video frames as arrays", run="lib", lib="imageio", pip="imageio"),
    dict(id="mediapipe", name="MediaPipe", kind="vision", lobe="occipital", color="#5dff9a", desc="hand and pose landmarks", run="lib", lib="mediapipe", pip="mediapipe"),
    dict(id="trimesh", name="trimesh", kind="cad", lobe="occipital", color="#4da3ff", desc="triangle meshes: load/measure/repair in memory", run="lib", lib="trimesh", pip="trimesh"),
    dict(id="soundfile", name="soundfile", kind="media", lobe="auditory", color="#ffd34d", desc="read audio samples and metadata", run="lib", lib="soundfile", pip="soundfile"),
    dict(id="librosa", name="librosa", kind="media", lobe="auditory", color="#ffd34d", desc="audio analysis: mel, onset, pitch", run="lib", lib="librosa", pip="librosa"),
    dict(id="pydub", name="pydub", kind="media", lobe="auditory", color="#ffd34d", desc="simple audio segment ops", run="lib", lib="pydub", pip="pydub"),
    dict(id="moviepy", name="MoviePy", kind="media", lobe="occipital", color="#ff4d9d", desc="compose and inspect video clips", run="lib", lib="moviepy", pip="moviepy"),
    dict(id="httpx", name="httpx", kind="browse", lobe="temporal", color="#9fd8ff", desc="HTTP client library (prefer tool fetch_url for safe page fetches)", run="lib", lib="httpx", pip="httpx"),
    dict(id="bs4", name="BeautifulSoup", kind="browse", lobe="temporal", color="#9fd8ff", desc="HTML/XML parsing", run="lib", lib="bs4", pip="beautifulsoup4"),
    dict(id="lxml", name="lxml", kind="browse", lobe="temporal", color="#9fd8ff", desc="fast HTML/XML toolkit", run="lib", lib="lxml", pip="lxml"),
    dict(id="playwright", name="Playwright", kind="browse", lobe="temporal", color="#9fd8ff", desc="headless Chromium (use tool browser_fetch)", run="lib", lib="playwright", pip="playwright"),
    dict(id="selenium", name="Selenium", kind="browse", lobe="temporal", color="#9fd8ff", desc="browser automation library", run="lib", lib="selenium", pip="selenium"),
    dict(id="aiohttp", name="aiohttp", kind="browse", lobe="temporal", color="#9fd8ff", desc="async HTTP client/server", run="lib", lib="aiohttp", pip="aiohttp"),
    dict(id="requests", name="requests", kind="browse", lobe="temporal", color="#9fd8ff", desc="HTTP library (prefer fetch_url)", run="lib", lib="requests", pip="requests"),
    dict(id="pypdf", name="pypdf", kind="docs", lobe="temporal", color="#9fd8ff", desc="PDF read helpers (use tool pdf_text)", run="lib", lib="pypdf", pip="pypdf"),
    dict(id="fitz", name="PyMuPDF", kind="docs", lobe="temporal", color="#9fd8ff", desc="fast PDF text and page extract (use tool pdf_text)", run="lib", lib="fitz", pip="pymupdf"),
    dict(id="openpyxl", name="openpyxl", kind="docs", lobe="temporal", color="#9fd8ff", desc="Excel workbooks (use tool xlsx_probe)", run="lib", lib="openpyxl", pip="openpyxl"),
    dict(id="docx", name="python-docx", kind="docs", lobe="temporal", color="#9fd8ff", desc="Word documents (use tool docx_text)", run="lib", lib="docx", pip="python-docx"),
    dict(id="pyarrow", name="PyArrow", kind="lib", lobe="parietal", color="#7bf0c4", desc="Arrow / Parquet columnar data", run="lib", lib="pyarrow", pip="pyarrow"),
    dict(id="sqlalchemy", name="SQLAlchemy", kind="lib", lobe="temporal", color="#9fd8ff", desc="SQL toolkit (no live DB URLs inside calc)", run="lib", lib="sqlalchemy", pip="sqlalchemy"),
    dict(id="polars", name="Polars", kind="lib", lobe="parietal", color="#7bf0c4", desc="fast DataFrame library", run="lib", lib="polars", pip="polars"),
    dict(id="duckdb", name="DuckDB", kind="lib", lobe="parietal", color="#7bf0c4", desc="in-process analytical SQL over files", run="lib", lib="duckdb", pip="duckdb"),
    dict(id="cvxpy", name="CVXPY", kind="lib", lobe="parietal", color="#7bf0c4", desc="convex optimisation modelling", run="lib", lib="cvxpy", pip="cvxpy"),
    dict(id="pulp", name="PuLP", kind="lib", lobe="parietal", color="#7bf0c4", desc="linear / integer programming", run="lib", lib="pulp", pip="pulp"),
    # ---- browse / verify / docs scripts (models call these via tool_run) ----
    dict(id="fetch_url", name="Fetch URL (text)", kind="browse", lobe="temporal", color="#9fd8ff", desc="GET a public http(s) page and return title, visible text and links (no localhost / private IPs)", run="pyargs", script="fetch_url.py", lib="httpx", pip="httpx", allow_url=True),
    dict(id="browser_fetch", name="Browser fetch (Playwright)", kind="browse", lobe="temporal", color="#9fd8ff", desc="headless Chromium render of a public page → visible text (JS-heavy sites)", run="pyargs", script="browser_fetch.py", lib="playwright", pip="playwright", allow_url=True),
    dict(id="pdf_text", name="PDF text extract", kind="docs", lobe="temporal", color="#9fd8ff", desc="extract text from a PDF in an allowed folder (PyMuPDF or pypdf)", run="script", script="pdf_text.py", lib="fitz", pip="pymupdf"),
    dict(id="docx_text", name="Word text extract", kind="docs", lobe="temporal", color="#9fd8ff", desc="extract paragraphs and tables from a .docx", run="script", script="docx_text.py", lib="docx", pip="python-docx"),
    dict(id="xlsx_probe", name="Excel probe", kind="docs", lobe="temporal", color="#9fd8ff", desc="list sheets and preview rows of an .xlsx", run="script", script="xlsx_probe.py", lib="openpyxl", pip="openpyxl"),
    dict(id="csv_stats", name="CSV / Parquet stats", kind="science", lobe="parietal", color="#7bf0c4", desc="shape, dtypes, nulls, describe and sample rows for CSV/TSV/Parquet", run="script", script="csv_stats.py", lib="pandas", pip="pandas"),
    dict(id="image_info", name="Image info", kind="vision", lobe="occipital", color="#5dff9a", desc="width, height, format, EXIF of an image file", run="script", script="image_info.py", lib="PIL", pip="pillow"),
    dict(id="audio_info", name="Audio info", kind="media", lobe="auditory", color="#ffd34d", desc="duration, channels, sample rate of an audio file", run="script", script="audio_info.py", lib="soundfile", pip="soundfile"),
    dict(id="unit_convert", name="Unit convert (pint)", kind="science", lobe="parietal", color="#7bf0c4", desc="convert physical units: args like '2.5 mile to km'", run="pyargs", script="unit_convert.py", lib="pint", pip="pint"),
    dict(id="json_check", name="JSON / schema check", kind="verify", lobe="cerebellum", color="#19f0c8", desc="parse JSON and optionally validate against a JSON Schema file", run="script", script="json_check.py", lib="jsonschema", pip="jsonschema"),
    dict(id="md_render", name="Markdown render", kind="docs", lobe="temporal", color="#9fd8ff", desc="render a Markdown file to plain text or HTML", run="script", script="md_render.py", lib="mistune", pip="mistune"),
    dict(id="transcribe", name="Speech to text (faster-whisper)", kind="speech", lobe="broca", color="#ffd34d", desc="turn the speech in an audio or video file into text, locally, with a model already on disk", run="script",
         script="transcribe.py", lib="faster_whisper", pip="faster-whisper", model_function="speech-stt"),
    # ---- formal / test / lint ----
    dict(id="lean", name="Lean 4", kind="formal", lobe="frontal", color="#e8793a", desc="check a formal proof or an exact finite statement (a file is accepted only if it compiles with no errors and no sorry)", run="exe", exe=["lean"], recipe="elan", version_must_pass=True),
    dict(id="lake", name="Lake (Lean build)", kind="formal", lobe="frontal", color="#e8793a", desc="build a Lean project with its dependencies", run="exe", exe=["lake"], recipe="elan"),
    dict(id="pytest", name="pytest", kind="test", lobe="cerebellum", color="#19f0c8", desc="run a project's tests", run="pymodule", lib="pytest", pip="pytest"),
    dict(id="hypothesis", name="Hypothesis", kind="test", lobe="cerebellum", color="#19f0c8", desc="property-based testing", run="lib", lib="hypothesis", pip="hypothesis"),
    dict(id="ruff", name="ruff", kind="lint", lobe="cerebellum", color="#19f0c8", desc="lint Python", run="pymodule", lib="ruff", pip="ruff"),
    dict(id="mypy", name="mypy", kind="lint", lobe="cerebellum", color="#19f0c8", desc="type-check Python", run="pymodule", lib="mypy", pip="mypy"),
    # ---- system / media / sandbox exes found or installable on this machine ----
    dict(id="git", name="Git (read-only)", kind="vcs", lobe="brainstem", color="#e6edf3", desc="read a repository: log, show, diff, status, archive of a commit", run="exe", exe=["git"]),
    dict(id="ffmpeg", name="FFmpeg", kind="media", lobe="occipital", color="#ff4d9d", desc="convert and inspect video and audio", run="exe", exe=["ffmpeg"], recipe="winget:Gyan.FFmpeg"),
    dict(id="ffprobe", name="FFprobe", kind="media", lobe="occipital", color="#ff4d9d", desc="probe media streams (codec, duration, bitrate) without decoding", run="exe", exe=["ffprobe"], recipe="winget:Gyan.FFmpeg"),
    dict(id="nvidia-smi", name="nvidia-smi", kind="gpu", lobe="cerebellum", color="#76b900", desc="GPU load, memory and temperature", run="exe", exe=["nvidia-smi"]),
    dict(id="docker", name="Docker", kind="sandbox", lobe="brainstem", color="#4da3ff", desc="an isolated container for untrusted checks (info/images/ps only)", run="exe", exe=["docker"]),
    dict(id="freecad", name="FreeCAD (headless)", kind="cad", lobe="occipital", color="#4da3ff", desc="run a FreeCAD Python script or open a document headless (FreeCADCmd): parametric CAD, TechDraw, exports",
         run="exe", exe=["FreeCADCmd", "freecadcmd"], host_exec=True),
    dict(id="rustc", name="rustc", kind="runtime", lobe="cerebellum", color="#dea584", desc="Rust compiler (--version / --print only unless cwd is trusted)", run="exe", exe=["rustc"]),
    dict(id="cargo", name="Cargo", kind="runtime", lobe="cerebellum", color="#dea584", desc="Rust package tool (metadata / check in trusted folders)", run="exe", exe=["cargo"], host_exec=True),
    dict(id="node", name="Node.js", kind="runtime", lobe="cerebellum", color="#68a063", desc="JavaScript runtime (--version; script run only in trusted folders)", run="exe", exe=["node"], host_exec=True),
    dict(id="aria2c", name="aria2c", kind="browse", lobe="brainstem", color="#9fd8ff", desc="download manager (use only into allowed work folders)", run="exe", exe=["aria2c"]),
    dict(id="trivy", name="Trivy", kind="verify", lobe="cerebellum", color="#19f0c8", desc="vulnerability / misconfig scanner for images and filesystems", run="exe", exe=["trivy"]),
    dict(id="hf", name="Hugging Face CLI", kind="ml", lobe="cerebellum", color="#b57bff", desc="hf hub download / scan / jobs (read-oriented commands)", run="exe", exe=["hf", "huggingface-cli"]),
    dict(id="uv", name="uv", kind="runtime", lobe="brainstem", color="#ffd34d", desc="fast Python package manager (pip-compatible)", run="exe", exe=["uv"]),
    dict(id="kubectl", name="kubectl", kind="sandbox", lobe="brainstem", color="#4da3ff", desc="Kubernetes CLI (read-only: get, describe, logs, version)", run="exe", exe=["kubectl"]),
    dict(id="cmake", name="CMake", kind="runtime", lobe="cerebellum", color="#064f8c", desc="build-system generator (--version / -N in trusted trees)", run="exe", exe=["cmake"]),
    dict(id="whisper", name="OpenAI Whisper CLI", kind="speech", lobe="broca", color="#ffd34d", desc="whisper.cpp / openai-whisper command-line transcription", run="exe", exe=["whisper"]),
    dict(id="ollama", name="Ollama CLI", kind="model", lobe="cerebellum", color="#b57bff", desc="list and show local models (prefer local_llm for prompts)", run="exe", exe=["ollama"]),
    dict(id="pandoc", name="Pandoc", kind="docs", lobe="temporal", color="#9fd8ff", desc="convert between document formats", run="exe", exe=["pandoc"], recipe="winget:JohnMacFarlane.Pandoc"),
    dict(id="jq", name="jq", kind="verify", lobe="cerebellum", color="#19f0c8", desc="JSON query processor", run="exe", exe=["jq"], recipe="winget:jqlang.jq"),
    dict(id="tesseract", name="Tesseract OCR", kind="vision", lobe="occipital", color="#5dff9a", desc="OCR text from images / scanned PDFs", run="exe", exe=["tesseract"], recipe="winget:UB-Mannheim.TesseractOCR"),
    dict(id="yt-dlp", name="yt-dlp", kind="media", lobe="occipital", color="#ff4d9d", desc="download media from public sites into an allowed folder", run="exe", exe=["yt-dlp", "yt_dlp"], pip="yt-dlp"),
]
KNOWN_GLOBS = [r"C:\Program Files\FreeCAD*\bin", r"%LOCALAPPDATA%\Programs\FreeCAD*\bin",
               r"C:\Program Files\Pandoc", r"C:\Program Files\Tesseract-OCR", r"C:\Program Files\nodejs",
               r"%LOCALAPPDATA%\Programs\Ollama", r"C:\Program Files\Docker\Docker\resources\bin"]
KNOWN_DIRS = [r"%USERPROFILE%\.elan\bin", r"%LOCALAPPDATA%\Programs\Python",
              r"C:\Program Files\Git\cmd", r"C:\Program Files\NVIDIA Corporation\NVSMI",
              r"%USERPROFILE%\.cargo\bin", r"%USERPROFILE%\.local\bin", r"%USERPROFILE%\tools\trivy",
              r"%ProgramFiles%\Python311\Scripts",
              r"C:\Program Files\nodejs", r"%LOCALAPPDATA%\Programs\Ollama",
              r"C:\Program Files\Docker\Docker\resources\bin", r"C:\Program Files\Google\Chrome\Application"]
_PROBE_LIBS = [
    "sympy", "mpmath", "numpy", "scipy", "z3", "flint", "gmpy2", "networkx", "pytest", "hypothesis", "ruff", "mypy",
    "psutil", "faster_whisper", "pandas", "matplotlib", "sklearn", "statsmodels", "seaborn", "numba",
    "jax", "xarray", "h5py", "pint", "uncertainties", "astropy", "skyfield", "symengine", "shapely", "pyproj",
    "geopandas", "jsonschema", "pydantic", "yaml", "toml", "orjson", "markdown", "mistune", "torch", "transformers",
    "sentence_transformers", "tiktoken", "nltk", "spacy", "PIL", "cv2", "skimage", "imageio", "mediapipe", "trimesh",
    "soundfile", "librosa", "pydub", "moviepy", "httpx", "bs4", "lxml", "playwright", "selenium", "aiohttp", "requests",
    "pypdf", "fitz", "openpyxl", "docx", "pyarrow", "sqlalchemy", "polars", "duckdb", "cvxpy", "pulp", "hashlib",
]
PROBE = ("import importlib.util as u,json,sys;L=%s;print(json.dumps(dict(v=sys.version.split()[0],libs={m:bool(u.find_spec(m) if m!='hashlib' else True) for m in L})))" % repr(_PROBE_LIBS))


def _run_quiet(argv, timeout=20, env=None):
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=NOWIN, env=env)
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            return None
        return (p.returncode, (out or "").strip())
    except OSError:
        return None


def which(name, extra_dirs=()):
    hit = shutil.which(name)
    if hit:
        return hit
    globbed = []
    for g in KNOWN_GLOBS:
        try:
            globbed += glob.glob(os.path.expandvars(g))
        except OSError:
            pass
    for d in list(extra_dirs) + [os.path.expandvars(x) for x in KNOWN_DIRS] + globbed:
        for ext in ("", ".exe", ".cmd", ".bat"):
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                return p
    return None


def fixed_drive_roots():
    if os.name == "nt":
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            return [("%s:\\" % chr(65 + i)) for i in range(26) if mask & (1 << i) and ctypes.windll.kernel32.GetDriveTypeW("%s:\\" % chr(65 + i)) == 3]
        except Exception:
            return [c + ":\\" for c in "CD"]
    return ["/"]


def python_candidates(extra=()):
    """interpreters worth knowing: this one, the one on PATH, and virtual environments found one or two folders below each fixed drive's top-level folders (envs/<name>, .venv, venv)"""
    cands = [sys.executable, shutil.which("python") or "", shutil.which("python3") or ""] + list(extra)
    pats = []
    for d in fixed_drive_roots():
        pats += [os.path.join(d, "*", "envs", "*", "Scripts", "python.exe"), os.path.join(d, "*", "envs", "*", "bin", "python"), os.path.join(d, "*", ".venv", "Scripts", "python.exe"),
                 os.path.join(d, "*", "venv", "Scripts", "python.exe"), os.path.join(d, "*", "*", ".venv", "Scripts", "python.exe")]
    for p in pats:
        try:
            cands += glob.glob(p)
        except OSError:
            pass
    seen, out = set(), []
    for c in cands:
        n = os.path.normcase(os.path.abspath(c)) if c else ""
        if n and n not in seen and os.path.isfile(c):
            seen.add(n)
            out.append(os.path.abspath(c))
    return out


class Registry:
    def __init__(self, data_dir, cfg):
        self.data, self.cfg = data_dir, cfg
        self.path = os.path.join(data_dir, "tools.json")
        self.custom_dir = os.path.join(data_dir, "tools", "custom")
        self.state = dict(scanned=0, pythons=[], tools={})
        try:
            self.state.update(json.load(open(self.path, encoding="utf-8")))
        except (OSError, ValueError):
            pass

    # ---------------------------------------------------------------- custom tools: engineered by an agent, found by their manifest
    def custom_specs(self):
        out = []
        for mf in glob.glob(os.path.join(self.custom_dir, "*", "manifest.json")):
            try:
                m = json.load(open(mf, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            tid = re.sub(r"[^a-z0-9_-]", "", str(m.get("id", "")).lower())[:40]
            if not tid or not isinstance(m.get("argv"), list) or not all(isinstance(x, str) for x in m["argv"]):
                continue
            out.append(dict(id="custom-" + tid, name=str(m.get("name") or tid)[:60], kind="custom", lobe=m.get("lobe") if m.get("lobe") in ("frontal", "parietal", "temporal", "occipital", "cerebellum", "brainstem", "broca", "auditory", "limbic") else "parietal",
                            color=m.get("color") if re.match(r"^#[0-9a-fA-F]{6}$", str(m.get("color", ""))) else "#b9c4cf", desc=str(m.get("description", ""))[:300], run="custom", folder=os.path.dirname(mf), argv=m["argv"],
                            built_by=m.get("built_by_job")))
        return out

    def specs(self):
        return SPECS + self.custom_specs()

    # ---------------------------------------------------------------- scanning
    def scan(self, force=False):
        """the tools found (cached). A stale cache is returned at once and refreshed in the background; only the very first scan, or force=True, makes the caller wait."""
        if not force and self.state.get("tools") and all(s["id"] in self.state["tools"] for s in self.specs()):
            if time.time() - self.state.get("scanned", 0) >= 600 and not getattr(self, "_refreshing", False):
                self._refreshing = True

                def refresh():
                    try:
                        self.scan(force=True)
                    finally:
                        self._refreshing = False
                import threading
                threading.Thread(target=refresh, daemon=True).start()
            return self.state
        pys = []
        for p in python_candidates([self.cfg.get("python") or ""] + list(self.cfg.get("extra_pythons") or [])):
            r = _run_quiet([p, "-I", "-c", PROBE], 25)
            if r and r[0] == 0:
                try:
                    d = json.loads(r[1].splitlines()[-1])
                    pys.append(dict(path=p, version=d["v"], libs=d["libs"]))
                except (ValueError, KeyError, IndexError):
                    pass
        pys.sort(key=lambda e: (-sum(e["libs"].values()), e["path"]))
        best = self.best_python(pys)
        tools = {}
        for s in self.specs():
            t = dict(available=False, path=None, version="", note="")
            kind = s["run"]
            if kind == "python":
                if best:
                    t.update(available=True, path=best["path"], version=best["version"])
            elif kind == "calc":
                t.update(available=bool(best), path=(best or {}).get("path"), version=(best or {}).get("version", ""), note="" if best else "needs a Python interpreter")
            elif kind in ("lib", "pymodule", "script", "pyargs"):
                lib = s.get("lib")
                if lib in (None, "hashlib"):
                    t.update(available=True, path=(best or {}).get("path") or "built in", version=(best or {}).get("version", "stdlib"))
                else:
                    env = self.env_with(pys, lib)
                    if env:
                        t.update(available=True, path=env["path"], version="in " + env["version"])
            elif kind == "read_file":
                t.update(available=True, path="built in")
            elif kind == "local_llm":
                try:
                    import urllib.request
                    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.5) as r:
                        t.update(available=r.status == 200, path="ollama", version="ollama is answering")
                except Exception:
                    t["note"] = "Ollama is not answering on 127.0.0.1:11434"
            elif kind == "exe":
                p = next((x for x in (which(n, self.cfg.get("extra_dirs") or []) for n in s["exe"]) if x), None)
                if p:
                    vflag = "-V" if s["id"] in ("ffmpeg", "ffprobe") else "--version"
                    v = _run_quiet([p, vflag], 15)
                    if not (v and v[0] == 0) and vflag != "--version":
                        v = _run_quiet([p, "--version"], 15)
                    t.update(available=True, path=p, version=((v[1].splitlines() or [""])[0] if v and v[0] == 0 else "")[:80])
                    if s.get("version_must_pass") and not (v and v[0] == 0):              # an elan shim with no toolchain is on PATH but cannot check anything
                        t.update(available=False, note="found, but `%s --version` failed: %s" % (s["exe"][0], (v[1].splitlines()[0][:160] if v and v[1] else "no answer")))
            elif kind == "custom":
                t.update(available=os.path.isdir(s["folder"]), path=s["folder"])
            if not t["available"] and (s.get("pip") or s.get("recipe")):
                t["note"] = ((t["note"] + "; ") if t["note"] else "") + "missing: can be installed (needs the owner's approval)"
            tools[s["id"]] = t
        self.state = dict(scanned=time.time(), pythons=pys, tools=tools)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)
        return self.state

    def best_python(self, pys=None):
        pys = pys if pys is not None else self.state.get("pythons", [])
        want = self.cfg.get("python")
        for e in pys:
            if want and os.path.normcase(e["path"]) == os.path.normcase(want):
                return e
        home = self.cfg.get("venv")
        for e in pys:
            if home and os.path.normcase(e["path"]).startswith(os.path.normcase(home)):
                pass
        return pys[0] if pys else None

    def env_with(self, pys, lib):
        """the best interpreter that has this library"""
        want = self.cfg.get("python")
        order = sorted(pys, key=lambda e: (0 if want and os.path.normcase(e["path"]) == os.path.normcase(want) else 1, -sum(e["libs"].values())))
        for e in order:
            if e["libs"].get(lib):
                return e
        return None

    def python_for(self, libs=()):
        """-> path of an interpreter that has all these libraries (else the best one)"""
        pys = self.state.get("pythons", [])
        want = os.path.normcase(self.cfg.get("python") or "")
        for e in sorted(pys, key=lambda e: (0 if want and os.path.normcase(e["path"]) == want else 1, -sum(e["libs"].values()))):
            if all(e["libs"].get(l) for l in libs):
                return e["path"]
        b = self.best_python()
        return b["path"] if b else None

    def spec(self, tid):
        return next((s for s in self.specs() if s["id"] == tid), None)

    def listing(self):
        st = self.scan()
        out = []
        for s in self.specs():
            t = st["tools"].get(s["id"], {})
            out.append(dict(id=s["id"], name=s["name"], kind=s["kind"], lobe=s["lobe"], color=s["color"], desc=s["desc"], available=bool(t.get("available")), path=t.get("path"), version=t.get("version", ""),
                            note=t.get("note", ""), installable=bool(s.get("pip") or s.get("recipe")), custom=s["run"] == "custom"))
        return out
