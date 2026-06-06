# AGENTS.md — zela_oracle_read_path_benchmark

Independent latency / feasibility benchmark for a WASM RPE platform co-located with
Solana validators. Active work: the M6 procedure (`procedures/m6_sim_recheck/`).

## Roles & sync
- The Codex app opens this folder directly, so it works on the live local tree (no
  remote/local sync gap).
- Loop: plan -> build -> commit locally (you write the message) -> report -> I
  approve -> push. Commit locally freely; PUSH requires explicit approval.

## Rules (hard)
- Plan before building (plan mode); get approval on the plan + files before editing.
- Verify before asserting — check source / docs; if unsure, say "unverified."
- Stop at any failure or design fork and report; do not auto-switch or hack around it.
- `git status --short` before any commit; stage only files you touched; never stage
  pre-existing untracked or gitignored files. Never push without explicit approval.
- All tracked content English-only; no personal names, no company / team names.

## Build
- `cargo build` · `cargo test` · `cargo build --target wasm32-wasip2`
- Workspace: `procedures/oracle_read` (M5 — do NOT modify), `procedures/m6_sim_recheck`
  (M6, active), `baseline_client`.

## Read before working
- `docs/M6_DESIGN_LOG_v2.6.md` — spec (§Q1 architecture, §Q5 PriceUpdateV2 decode / gates).
- `docs/ubiquitous-language.md` — canonical terms; use these spellings.
- `docs/SESSION_HANDOFF_2026-06-04.md` — current state, locked decisions, ground-truth
  constants, next actions, the approved C1 task.
- `docs/M6_WORKFLOW_RESEARCH.md` — multi-session workflow notes.

## References the agent uses (present locally; gitignored, so not committed)
Two DISTINCT skill sources — do not conflate them:
- `docs/skills/` — personal engineering-discipline skills: grill-me, deep-modules,
  ubiquitous-language, ai-coding-handout. Apply these to HOW you build.
- `vendor/zela-ai-skills/` — Zela's OFFICIAL skills repo (a Zela repo, like the two
  below). Consult it when writing Zela procedures.
- `vendor/zela-demo/` — Zela example procedures (e.g. the `block_time` chrono / wall-
  clock precedent).
- `zela-std` — the WASM RPE SDK; a Cargo dependency (`cargo build` fetches it). Key
  verified facts are in the design log / handoff.
