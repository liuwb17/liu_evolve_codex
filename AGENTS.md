# Handoff instructions for coding agents

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
- Use a new experiment directory for each comparison; one writer per run and no competing timed evaluations.
- Historical docs can be stale. Actual source and per-run protocol/ledger/state take precedence over old status prose.
- This handoff does not authorize parallel paid experiments or publication of this private repository.
