"""a stand-in for an agent CLI: reads the prompt on stdin and answers according to its first argument"""
import json, sys, time

mode = sys.argv[1] if len(sys.argv) > 1 else "patch"
prompt = sys.stdin.read()
FIX = "def add(a, b):\n    return a + b\n"
if mode == "patch":
    print(json.dumps(dict(files={"calc.py": FIX}, notes="fixed the sign")))
elif mode == "badpatch":
    print(json.dumps(dict(files={"calc.py": "def add(a, b):\n    return a * b\n"}, notes="oops")))
elif mode == "protected":
    print(json.dumps(dict(files={"test_calc.py": "print('ok')\n"}, notes="made the test pass by editing it")))
elif mode == "traverse":
    print(json.dumps(dict(files={"../evil.py": "x = 1\n"}, notes="")))
elif mode == "nojson":
    print("Sure! Here is how I would fix it: change the minus to a plus.")
elif mode == "limit":
    sys.stderr.write("ERROR: You've hit your usage limit. visit settings to purchase more credits or try again at 8:47 PM.\n")
    sys.exit(1)
elif mode == "hang":
    time.sleep(120)
elif mode == "plan":
    print(json.dumps(dict(steps=[dict(objective="fix the sign error in calc.py", needs=["code"])])))
elif mode == "approve":
    print(json.dumps(dict(approve=True, reasons=[])))
elif mode == "reject":
    print(json.dumps(dict(approve=False, reasons=["no explanation of the change"])))
elif mode == "answer":
    print(json.dumps(dict(answer="rain taps the glass / the street holds its breath / grey light, slowly", notes="three lines")))
elif mode == "toolcalls":                                              # asks the computer to integrate, then answers from the tool's output
    if "TOOL RESULTS" in prompt:
        print(json.dumps(dict(answer="the integral is pi/2 (checked with the local calculator)", notes="used calc")))
    else:
        print(json.dumps(dict(tool_calls=[dict(tool="calc", code="import sympy as sp\nx=sp.symbols('x')\nprint(sp.integrate(sp.sin(x)**2,(x,0,sp.pi)))")])))
elif mode == "closes":                                                 # claims everything, including what only an independent job may claim
    print(json.dumps(dict(answer="G06 closed: coverage = 87%", notes="ok", result=dict(claim_class="INDEPENDENTLY_CONFIRMED", first_open_premise="", requested_transition={"G06": "CLOSED-AUDITED"}, values={"bound": "1/8"}))))
elif mode == "opens":
    print(json.dumps(dict(answer="not closed", notes="", result=dict(claim_class="CODE_VERIFIED", first_open_premise="the vector orientation drift bound", values={"bound": "?"}))))
elif mode == "forbidden":
    print(json.dumps(dict(files={"secret/x.py": "x = 1\n"}, notes="wrote outside scope")))
elif mode == "architect":                                              # a scripted architect / critic: answers by the PASS marker in the prompt
    import re
    tgt = (re.search(r"TARGET=(\S+)", prompt) or [None, ""])[1]
    full = "ISSUE-FIXED" in prompt
    reqs = [dict(id="R1", text="the bracket folds flat", priority="must", source="user", acceptance="geometry check"), dict(id="R2", text="total mass under 8 kg", priority="must", source="user", acceptance="mass check")]
    jobs_bad = [dict(key="tests", title="Write acceptance tests", goal="make the acceptance script run", kind="general", rubric="the script exists", covers=[], steps=[dict(objective="describe tests", needs=["write"])]),
                dict(key="build", title="Build the bracket model", goal="model it using the mass-calc tool", kind="code", target=tgt, check="python tools/acceptance.py", covers=["R1"], depends_on=["tests"], phase="2 Build",
                     fixtures={"tools/acceptance.py": "print('ok')\n"}, steps=[dict(objective="write the model", needs=["code"])])]
    jobs_good = [dict(jobs_bad[0], covers=["R2"]), dict(jobs_bad[1])]
    if "PASS=interview" in prompt:
        print(json.dumps(dict(reply="Understood: a fold-down bracket under 8 kg. Which anchor type?", blueprint=dict(title="Fold-down wall bracket", summary="A fold-down wall bracket for a 45 kg compressor.",
              feasibility=dict(verdict="feasible", why="standard steel fabrication"), goals=["hold 45 kg"], requirements=reqs, open_questions=[dict(id="Q1", question="Which anchor type?", why="changes the plate", default="M12 chemical anchors")],
              reference_systems=[dict(name="vendor-firmware.bin", why="its load table", how_to_study="decompile")]))))
    elif "PASS=study" in prompt:
        if "TOOL RESULTS" in prompt:
            print(json.dumps(dict(findings=[dict(about="vendor-firmware.bin", text="the load table starts at 0x400 and lists 12 entries")])))
        else:
            print(json.dumps(dict(tool_calls=[dict(tool="calc", code="print(0x400)")])))
    elif "PASS=design" in prompt:
        print(json.dumps(dict(design=dict(architecture="plate + hinge + prop arm", components=[dict(name="plate", responsibility="carries the load", interfaces=["anchors"], tech="6 mm mild steel")],
              hooks=[dict(name="mass-check", when="every build", does="fails above 8 kg")], tools_needed=[dict(name="mass-calc", description="print the mass in kg of a steel plate from its dimensions in mm", examples=[dict(args=["300", "200", "6"], expect="2.8")])]),
              feasibility=dict(verdict="feasible", why="ok"), risks=[dict(risk="weld distortion", mitigation="jig")])))
    elif "PASS=plan" in prompt:
        print(json.dumps(dict(plan=dict(jobs=jobs_bad), verification="run the acceptance script and weigh the model", effort=dict(local_tool_runs=4, subscription_runs=3))))
    elif "PASS=critique" in prompt:
        blockers = '"blockers": []' not in prompt.replace(" ", "")
        print(json.dumps(dict(verdict="revise" if blockers else "ready", issues=[dict(severity="blocker", area="coverage", issue="R2 is not covered by any job", fix="ISSUE-FIXED: add R2 to the tests job")] if blockers else [])))
    elif "PASS=revise" in prompt:
        print(json.dumps(dict(blueprint=dict(plan=dict(jobs=jobs_good)))))
    else:
        print(json.dumps(dict(answer="?")))
elif mode == "vacuous":                                                # an architect whose first plan has a test that passes on an empty project (must be caught), then fixes it
    import re
    tgt = (re.search(r"TARGET=(\S+)", prompt) or [None, ""])[1]
    reqs = [dict(id="R1", text="the bracket folds flat", priority="must", source="user", acceptance="check")]
    job = lambda fx: dict(key="build", title="Build the bracket model", goal="model it in brackets.py", kind="code", target=tgt, check="python tools/acceptance.py", covers=["R1"], phase="1 Build",
                          fixtures={"tools/acceptance.py": fx}, steps=[dict(objective="write brackets.py", needs=["code"])])
    if "PASS=interview" in prompt:
        print(json.dumps(dict(reply="Understood.", blueprint=dict(title="Bracket", summary="A bracket.", feasibility=dict(verdict="feasible", why="ok"), requirements=reqs, open_questions=[dict(id="Q1", question="Which anchor?", default="M12")]))))
    elif "PASS=design" in prompt:
        print(json.dumps(dict(design=dict(architecture="plate", components=[dict(name="plate", responsibility="load", interfaces=[], tech="steel")]), feasibility=dict(verdict="feasible", why="ok"))))
    elif "PASS=plan" in prompt:
        print(json.dumps(dict(plan=dict(jobs=[job("print('ok')\n")]), verification="run it")))
    elif "PASS=critique" in prompt:
        print(json.dumps(dict(verdict="ready", issues=[])))
    elif "PASS=revise" in prompt:
        print(json.dumps(dict(blueprint=dict(plan=dict(jobs=[job("import brackets\nassert brackets.fold() == 'flat'\nprint('ok')\n")])))))
    elif "PASS=repair" in prompt and "SPLIT-ME" in prompt:
        mk = lambda n, g: dict(key=n, title="Build the bracket model: " + n, goal=g, kind="code", target=tgt, check="python tools/acceptance.py", covers=["R1"], steps=[dict(objective="write " + n, needs=["code"])],
                               fixtures={"tools/acceptance.py": "import brackets\nassert brackets.fold() == 'flat'\nprint('ok')\n"})
        print(json.dumps(dict(note="too big: split", give_up=False, jobs=[mk("geometry", "write the geometry part of brackets.py"), mk("checks", "write the checks part of brackets.py")])))
    elif "PASS=repair" in prompt:
        print(json.dumps(dict(note="the goal was too vague", give_up=False, job=dict(key="build", title="Build the bracket model (clearer)", goal="write brackets.py with fold() returning 'flat'", kind="code", target=tgt, check="python tools/acceptance.py", covers=["R1"],
                  fixtures={"tools/acceptance.py": "import brackets\nassert brackets.fold() == 'flat'\nprint('ok')\n"}, steps=[dict(objective="write brackets.py", needs=["code"])]))))
    else:
        print(json.dumps(dict(answer="?")))
elif mode == "gemini":                                                 # a CLI with models: -m <model>; some are out of quota, one does not exist
    model = sys.argv[sys.argv.index("-m") + 1] if "-m" in sys.argv else "default"
    import os
    with open(os.environ.get("FAKE_LOG", os.devnull), "a") as lf:
        lf.write(model + "\n")
    if model in os.environ.get("FAKE_BAD", "").split(","):
        sys.stderr.write("Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests. Please retry in 2h5m10.5s.\n")
        sys.exit(1)
    if model == "gone":
        sys.stderr.write("{ code: 404 } An unexpected critical error occurred\n")
        sys.exit(1)
    print(json.dumps(dict(answer="answered by " + model, notes="")))
elif mode == "argv":                                                   # a CLI that logs the words it was started with, so a test can see --model / --effort
    import os
    with open(os.environ.get("FAKE_LOG", os.devnull), "a") as lf:
        lf.write(" ".join(sys.argv[2:]) + "\n")
    print(json.dumps(dict(answer="ok", notes="")))
elif mode in ("agentic_fix", "agentic_cheat", "agentic_idle"):                # an agent that works in the current folder itself (like Claude Code in the container)
    sys.stdin.read()
    if mode == "agentic_fix":
        open("calc.py", "w").write("def average(xs):\n    return sum(xs) / len(xs)\n")
    elif mode == "agentic_cheat":
        open("calc.py", "w").write("def average(xs):\n    return 4\n")
        open("test_calc.py", "w").write("print('ok')\n")
    print("I changed calc.py and the check passes.")
elif mode == "echo":                                                   # proves what the agent was (not) shown
    print(json.dumps(dict(answer=prompt[:6000], notes="echo")))
else:
    sys.exit(2)
