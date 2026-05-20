# Route test results — M5 static-routing measurements

Per-region client e2e latency from a single Prague client using `zela-route-by: static <label>` HTTP header, plus default auto-routing.

Each `*_session.txt` file contains 100 lines, one `wall_clock_us` integer per line. First 5 runs are connection warmup — exclude from analysis. Warm sample = last 95 runs.

| File | Route | Warm p50 | Warm stdev |
|---|---|---|---|
| `fr2_session.txt` | Frankfurt (`fr2`) | 18.6 ms | 1.1 ms |
| `dx1_session.txt` | Dubai (`dx1`) | 227.7 ms | 7.5 ms |
| `ewr_session.txt` | Newark NJ (`ewr`) | 229.7 ms | 11.3 ms |
| `slc_session.txt` | Salt Lake City (`slc`) | 322.9 ms | 37.4 ms |
| `tyo_session.txt` | Tokyo (`tyo`) | 461.8 ms | 19.3 ms |
| `auto_session.txt` | Default (leader-aware) | 18.5 ms p50, 460.9 ms p95 | 144.6 ms |

Collected 2026-05-19 using `analysis/post_hoc/route_test_session.py`, 100 runs per route with `requests.Session()` connection reuse. Used to produce `docs/figures/fig2_per_region.png` and to calibrate the latency-tier thresholds in `leader_correlation_v3.py`.
