# Post-hoc analysis scripts

Run after `orchestrator/orchestrate.py` data collection to derive M5 routing and correlation findings.

- `route_test_session.py` — controlled per-region latency measurement using `zela-route-by: static <label>` HTTP header. Produces per-region latency distributions used as classification reference for leader correlation.
- `fetch_new_slots.py` — primes cache mapping Solana slot → leader pubkey via `getSlotLeaders`.
- `fill_missing_validators.py` — primes cache mapping leader pubkey → geographic location via validators.app.
- `leader_correlation_v3.py` — per-run correlation pipeline. Joins `context_slot → leader pubkey → leader geography → expected Zela tier`, compares to observed tier inferred from client e2e latency, reports match rate per slot offset and confusion matrix.

Caches and intermediate outputs saved to `../../leader_correlation_results/`.

Reproduction order:
1. `python route_test_session.py --route fr2 --runs 100` (repeat per region)
2. `python fetch_new_slots.py`
3. `python fill_missing_validators.py`
4. `python leader_correlation_v3.py`
