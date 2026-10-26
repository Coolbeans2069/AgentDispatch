"""launcher for agents: `python /path/to/agentdispatch/run_mcp.py stdio --profile full` (works from any folder; no PYTHONPATH needed)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if "--data" not in sys.argv:
    sys.argv[1:1] = ["--data", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")]
from agentdispatch.__main__ import main
sys.exit(main())
