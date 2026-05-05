#!/usr/bin/env bash
# Scheduled wrapper that runs the orchestrator and parks output in
# zela_datasets/dataset_YYYY_MM_DD_HHMM/. Called from crontab.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STAMP=$(date +%Y_%m_%d_%H%M)
LOG="$REPO/logs/cron_$STAMP.log"

mkdir -p "$REPO/logs" "$REPO/zela_datasets"
cd "$REPO"

# Cron's environment is minimal; set a sensible PATH and the user's locale.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export LANG="${LANG:-C.UTF-8}"

# Load benchmark credentials / config.
set -a
# shellcheck disable=SC1091
source .env
set +a

{
    echo "=== cron_run.sh start $(date --iso-8601=seconds) ==="
    python3 orchestrator/orchestrate.py --runs 100 --output-dir zela_datasets
    echo "=== orchestrator exited $? ==="
} >/dev/null 2>>"$LOG"

# Orchestrator creates zela_datasets/run_YYYYMMDD_HHMMSS/. Rename it to
# the dataset_YYYY_MM_DD_HHMM scheme used by the committed datasets.
NEW_RUN=$(ls -1dt zela_datasets/run_* 2>/dev/null | head -n1 || true)
if [ -n "${NEW_RUN:-}" ] && [ -d "$NEW_RUN" ]; then
    DEST="zela_datasets/dataset_$STAMP"
    mv "$NEW_RUN" "$DEST"
    echo "renamed $NEW_RUN -> $DEST" >>"$LOG"
else
    echo "WARNING: no zela_datasets/run_* directory found after orchestrator exit" >>"$LOG"
fi

echo "=== cron_run.sh end $(date --iso-8601=seconds) ===" >>"$LOG"
