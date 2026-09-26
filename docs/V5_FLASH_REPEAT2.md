# v5 Flash independent repetition 2

User authorized a parallel repeat on 2026-09-25. This is a new problem-only
cold start with 256 total Chat Completions requests to `deepseek-flash`.
Failed requests count toward the limit; no aggregate token cap is imposed.
The first Flash run's solver, candidate archive, scores and API responses are
not supplied to the new model. Independent model samples create a new lineage;
this is not a rerun of the already frozen solver and does not set an API seed.

- Run: `runs/v5_flash_256_repeat2_20260925`
- Terminal output: `runs/v5_flash_256_repeat2_system_1000_20260925`
- Entry: `python -B scripts/v5_flash_repeat2.py --launch`
- Status: `python -B scripts/v5_status.py --run-dir runs/v5_flash_256_repeat2_20260925`

Before the first API call, the worker checks exact equality with the original
Flash development protocol: engine/provider hashes, model, call budget, public
input hashes and 40/10 split, repeat counts, selection margins, Docker image,
and timing settings. This retains thinking enabled with low effort and 65,536
maximum output tokens; six case workers each receive 1 CPU and 512 MiB, with a
4.8-second solver budget and 4.95-second hard limit.

API activity can overlap with v5 Pro. All evaluation, including compilation and
terminal Rust rescoring, shares the existing exclusive evaluation lock. This
affects elapsed scheduling time but preserves per-solver resource settings.
Original pinned framework and running Pro orchestration files are not changed.

Normal workflow: public evolution -> public validation/freeze -> source audit ->
official Rust-generated 1000-private-case terminal evaluation, compared with
the existing matched kusano baseline. Private cases never feed back into this
run's evolution. The dataset is already a repeatedly observed research benchmark,
not a new unseen benchmark. Two runs alone do not establish statistical certainty.
