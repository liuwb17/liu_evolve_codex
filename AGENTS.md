# Handoff instructions for coding agents

## v5.2 scope agreed in this session

- The original GitHub AHC001-tailored v5 is v5.0; baseline generalization at 052af15 is v5.1; the current release is v5.2. Package version 0.5.2, run/report protocol 5.2. Keep v5_1_* filenames/classes and task-bundle schema 5.1; old runs require their pinned source.
- Plan is optional and off by default. One problem-only call generates four short algorithm directions, one per initial cold-start lineage. No shared solver source or generated diagnostics. Record Plan separately from the evolution call/token budget and recover its saved response without retrying failed calls. Plan initialization requires four valid directions, without the legacy 20-attempt cap.
- para=None retains serial scheduling; para=4 uses four fixed lineages and concurrent LLM rounds (best local/redesign/complementary crossover, plus a random other-lineage champion alternating redesign and crossover with best as donor). Initialize concurrently, evaluate candidates sequentially, persist round snapshots and call reservations. Finish the last round and its one-shot repairs beyond the requested call budget; token budget remains a hard limit. Count every request. No other para values are supported.
- Every evaluated individual stores runtime_ratio = mean official process execution time / task time limit (all evaluated cases and algorithm seeds, no duplicate precheck counting). In para=4, best archive runtime_ratio <= 0.30 overrides task 4 with runtime on that best individual, without donor; global round alternation still advances. Incumbent-discovery timestamps are not currently measured.
- failure_experience is a strict bool, default false. When enabled, retain up to eight unsuccessful attempts on each exact main parent (never inherit to children or donors), using the same call's hypothesis plus measured outcome. Equal/worse mean or invalid candidates count; strict improvement is success regardless of global incumbent/promotion margin. Include a snapshot in every parent-based prompt, with parallel-round results visible only to later batches. No extra LLM summaries and no invented insights for transport failures without a usable hypothesis.
- Evaluation targets this 9950X3D host (16 SMT pairs, 32 logical CPUs). Reserve two complete groups for compile/scoring; lease the remaining 14 groups across processes via Docker names. Pin each solver to one logical CPU and reserve its sibling. Wait before official timing when all groups are occupied. No fixed --workers. Never steal stale leases without verifying the owner/container has stopped. See v5_1_cpu.py; Docker affinity and full-pool waiting were tested locally, full ALE integration was not. Windows/WSL host contention and hidden CCD mappings remain outside this isolation guarantee.

- Work only from this GitHub-derived checkout (baseline `43a371a`), not the other local v5 forks.
- Execution repair: `v5_1_execution.py` replaces the private ALE batch runner's global sync with per-file fsync and adds a container watchdog. Runtime protocol `batch-file-fsync-watchdog-1` prevents cache mixing. Infrastructure timeouts must abort, never become candidate scores. Old suites stay immutable except stop markers; an explicitly authorized recovery goes to a new directory through `scripts/v52_migrate_sync.py`, with carried measurements and paid responses audited in migration.json. Three-task two-case Docker preflight and injected wait failure passed locally; this is not a full benchmark validation.
- See `docs/V5_1.md`. v5.1 supports ALE-Bench batch/non-interactive problems and C++17 solvers.
- Default training seeds are official public 0–49; the public subset is configurable. No validation split or validation selection.
- Freeze the evaluated training incumbent before reading/generating/evaluating official private seeds. Private scores never guide evolution.
- Every solver/edit response must declare JSON boolean is_randomized. Deterministic candidates use one evaluation, randomized candidates use seeds 0/1/2 supplied through AAD_RANDOM_SEED. No repeat-confirmation gate; promote by mean raw score in the official maximize/minimize direction (margin defaults to zero). Private testing uses the same one-or-three policy.
- Historical v5 modules and checkpoints retain their old behavior. New v5.2 runs use protocol version 5.2 and separate directories.
- ALE tools run in Docker under Linux/WSL. Tests are offline/mocked unless explicitly identified as Docker integration tests.

## Historical v3/v5 instructions

Read README.md before modifying this research project. It documents actual v3/v5 behavior, known limitations, and paths that are not portable.

- v3/v5 controllers are Python; historical generated solvers are C++17. Python-solver migration is not implemented.
- Experiments were explicitly stopped/paused by the user. Do not start model requests, resume runs, remove stop markers, or expand budgets without explicit authorization.
- Public 50 inputs are split into 40 training and 10 final validation cases. Never use private 1000-case data or feedback during evolution.
- Keep frozen runs immutable. Protocol/implementation changes need new runs or explicit audited migrations.
- Do not edit model-produced historical candidate sources and continue claiming pure model provenance.
- Do not run untrusted candidates natively; preserve Docker isolation, time limits, independent scoring and source audit.
- Count failed and interrupted model requests; unknown usage is not free usage.
- Never commit secrets, raw model traces, run artifacts, benchmark data or human reference sources. Check the staged diff and file list before every push.
- Start with python -m unittest discover -s tests -q (offline/mocked; no model fees).
- Unit tests passing does not mean Docker/data/provider setup or clean-clone private evaluation is ready. See README known portability limitations.
- Do not silently change timer/scoring/cache semantics. Explain and version changes.
- Use a new experiment directory for each comparison; one writer per run. Current v5.1 timed cases may run concurrently only through its exclusive core pool; historical runners must not compete with them.
- Historical docs can be stale. Actual source and per-run protocol/ledger/state take precedence over old status prose.
- This handoff does not authorize parallel paid experiments or publication of this private repository.
