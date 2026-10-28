"""Run the demo's tests; exit 1 if any fails. The MCP end-to-end test skips itself when the mcp SDK is missing."""
import glob
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
failed = 0
for f in sorted(glob.glob(os.path.join(here, "test_*.py"))):
    r = subprocess.run([sys.executable, f], capture_output=True, text=True, timeout=600)
    out = (r.stdout + r.stderr).strip().splitlines()
    print("%-22s %s    %s" % (os.path.basename(f), "OK" if r.returncode == 0 else "FAIL", out[-1] if out else ""))
    if r.returncode:
        failed += 1
        print("\n".join(out[-40:]))
sys.exit(1 if failed else 0)
