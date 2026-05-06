# Legacy sequential datasets

These datasets were collected with the original sequential loop of 10
`getAccountInfo` calls (see git log: the commit that introduced
`getMultipleAccounts` in `procedures/oracle_read/src/lib.rs` and
`baseline_client/src/main.rs`).

Schema has `wall_clock_elapsed_us` per feed in `feeds.csv` (different
from the new batch schema).

Headline from these data: median ratio 200× server-side Zela vs
end-to-end baseline. Slot consistency: Zela 91% / Baseline 13%
(one-slot consistency).

Both metrics reflect sequential measurement methodology and are not
directly comparable to batch v2 numbers. Kept for continuity reference.

For analysis, use:
    python3 analysis/analyze.py --mode sequential <path>
