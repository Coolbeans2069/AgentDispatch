# Agency (Phase 2) - design spec

Status: design document. Stage 0 (jobs, registry, dispatcher, gate with fake agents) and Stage 1 (real agents, patch protocol, Docker check runner, planner and reviewer) are implemented.

## 0. Purpose and scope
A cross-platform AI agency: a task goes in once; the agency splits it, picks the right agent for each step (Claude, Codex, Gemini CLI, Grok CLI, pull agents, local Ollama), within what each agent has left, reviews the result with a different agent plus objective checks, and either delivers it, merges it (safe repos only) or puts it in front of the owner.
It extends what exists - the broker (notice board, attempt ledger, router, night shift, tool index) and the engine room (agent detection, limits, GPU/CPU/RAM headroom) - and adds no third service.
Job kinds, in build order: **code** (change a repo), **production** (a MediaStudio render / repair), **general** (research, writing, automation).

Non-goals for 0.4.0: a web UI beyond the engine room panel; running agents on other machines; changing MediaStudio's pipeline logic.

## 1. Jobs and the agent registry
### 1.1 Job
Stored as events on the board (type `job.*`) with projection tables `jobs` and `steps` in the same SQLite file; each job also has a board post (kind `status`, tag `job`) so it shows on the bulletin page and is searchable.
Fields: `id` (J-000001), `title`, `goal`, `kind`, `target` (repo path or project id), `acceptance` (see 1.2), `budget` (`max_steps`, `max_attempts_per_step`, `wall_min`, `max_runs`), `priority` (0-3), `owner`, `created`, `state`.
States: `queued -> planning -> running -> reviewing -> ready | merged | delivered | failed | cancelled` (`blocked` when it waits for the owner).
Steps: `id` (S-1..), `objective`, `needs` (tags such as `code`, `review`, `long-context`, `vision`), `state` (`todo|assigned|running|done|failed|skipped`), `attempts`, `agent`, `result_ref`.
The planner (a strong agent, or the Gemini orchestrator) returns steps as JSON; the broker validates it (size caps, no duplicate objectives, `needs` in the known set). The attempt ledger already blocks repeats and escalates after 3 failures.

### 1.2 Acceptance
One of: `check` (a command run in the sandbox that must exit 0; reuses the night-shift check format and its command allow-list), `rubric` (text a reviewer agent scores pass/fail with reasons), or both. A code job without a check is refused unless a rubric is given.

### 1.3 Agent registry (`agents.json`, versioned in `dev/`)
Per agent: `id`, `kind` (`cli|app|local`), `run` (command template with `{prompt_file}`, `{cwd}`, `{model}`; timeout; env), `strengths` (tags), `cost` (`free|local|cheap|plan|premium`), `concurrency`, `daily_cap` (runs), `sandbox` (`copy|container|none`), `limits_source` (`engineroom:<name>|learned|none`), `enabled`.
Availability = connected (engine room `/agents`) AND not cooling down AND under concurrency/daily cap AND resources allow (local models need GPU/RAM headroom from the engine room).
Cooldowns are learned: a run that reports a limit / 429 / "usage" error puts the agent in cooldown (exponential, capped at 6 h) and is not counted as a step failure. Claude and pull agent percentages cannot be read, so they use conservative pacing and the learned cooldown only.

## 2. Dispatcher, review gate, apply flow
### 2.1 Dispatcher
Loop (default every 20 s, one thread): take the next runnable step by priority/age; candidates = registry filtered by `needs` and availability; order by cost class (local first, then cheap, plan, premium) and by `tier_for()` (escalate after failures); write `board_attempt`; run the adapter in an isolated workspace; hard timeout with process-tree kill; attach the transcript; `board_result`. Global caps: `max_parallel` runs, quiet hours (default none), and never start heavy local work while the engine room reports a production render holding the GPUs.
Push first: unattended runs of CLIs with a headless mode (`claude -p`, `codex exec`, `gemini -p`, Ollama via the router). Pull second: an open session may claim a job through the existing MCP tools (`board_claim`), which marks the step `running` by that actor.

### 2.2 Review gate
A step (and then the job) is accepted only when all hold: objective checks pass (the acceptance `check`; plus `py_compile`, `devtrack verify`, secret redaction on the diff); an independent reviewer - a different agent id than the author - approves the diff against the acceptance rule; guard checks pass (no protected path touched, diff under `max_diff_lines`, no new network / process-spawn calls unless the step asked for them).
Protected paths: the agency package's gate and approval code, its tests, `agents.json` policy fields, `dev/` hash files; changing them always needs the owner. Their sha256 are checked when the dispatcher starts; a mismatch stops it.

### 2.3 Apply flow
- Safe repos (`agentdispatch`, `engineroom`; list in `agency.json`): rollback tag -> fast-forward merge -> re-run the full suite on the result -> `devtrack snapshot` -> post the merge with hashes to the board. If the post-merge suite fails the merge is reverted automatically and the job fails.
- MediaStudio live code and any other repo: state `ready` + a needs-you card; nothing moves until the owner approves (owner token; agents cannot).
- Production and general jobs: no merge; the result is delivered (`delivered`) with its evidence.
Guardrails: kill switch file `data/agency_off`; `max_merges_per_day` (default 5); `auto_merge` is OFF until the owner sets it.

## 3. Engine room, digest, rollout, testing
### 3.1 Engine room
- `GET /agents` (new): per agent `{id, running, n, limit_age_min, limit_pct|null, load}`; the dispatcher reads it.
- Jobs panel (new): counts by state, a needs-you list with Approve / Reject / Retry / Cancel (these call broker owner endpoints), and the dispatcher's status line via the existing `/say`.
### 3.2 Needs-you digest
One query: jobs in `ready`, `blocked`, `failed`, plus pending tool approvals. Shown in the panel and written daily to `data/digest/YYYY-MM-DD.md`.
### 3.3 Rollout (each stage released, tested and hash-verified before the next)
0. Skeleton: jobs, registry, dispatcher, gate, fake agents only; plans and assigns, executes nothing real. (0.4.0-alpha)
1. Real code runs on a scratch clone of the engine room: Ollama, then `codex exec`, `gemini -p`, `claude -p`; review gate; simulated merge.
2. `auto_merge` on for the safe repos.
3. Production jobs: the soak runner becomes a job type; watchdog incidents become jobs.
4. General solver: planner + rubric reviewer.
### 3.4 Testing
Fake adapters (deterministic): selection, escalation, cooldown, caps, state machine, gate decisions, protected-path and hash checks, kill switch. Live: one cheap task per CLI. Chaos: limit hit, hang, protected-file edit, secret in diff, failing diff, post-merge failure + revert, Studio busy. Then the repeated review loop used for earlier broker releases.

## 4. Risks and open points
- Headless runs spend plan usage: per-agent `daily_cap`, default low.
- Claude / pull agent limits are unknown: cooldown learning is the only signal.
- Windows process handling: kill the whole tree; never leave a CLI orphaned.
- The agency must not starve production: it checks engine room headroom before local-model runs.
- The lean tool index can miss: REA's index missed one query in the hard set, so an agent must be allowed to fall back to `tool_search` with different wording and to report what it could not find.
- Hardware incidents: production resumes only on the owner's word, and the agency inherits that switch (`agency_off`).
