"""run every test file; exit 1 if any fails"""
import glob, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
files = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
bad = 0
for f in files:
    r = subprocess.run([sys.executable, f], cwd=ROOT, capture_output=True, text=True, timeout=900)
    last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    print("%-28s %s  %s" % (os.path.basename(f), "OK  " if r.returncode == 0 else "FAIL", last))
    if r.returncode:
        bad += 1
        print((r.stdout + r.stderr)[-1500:])
sys.exit(1 if bad else 0)
