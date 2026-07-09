#!/usr/bin/env bash
# docker-entrypoint.sh
# Validates volumes, rewrites relative asset paths to absolute /input/... paths,
# then delegates to build_app.py.  Any extra arguments are forwarded to build_app.py
# (e.g. --verbose, --tasks assembleRelease).
set -euo pipefail

# ── Configurable paths (override via -e in docker run if needed) ──────────────
INPUT_DIR="${INPUT_DIR:-/input}"
OUTPUT_DIR="${OUTPUT_DIR:-/output}"
KEYSTORE_DIR="${KEYSTORE_DIR:-/keystores}"
TEMPLATE_DIR="${TEMPLATE_DIR:-/template}"
CONFIG_FILE="${CONFIG_FILE:-${INPUT_DIR}/config.json}"

# ── Colour helpers ────────────────────────────────────────────────────────────
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BOLD}[build]${NC} $*"; }
warn() { echo -e "${YELLOW}[build] WARNING:${NC} $*"; }
die()  { echo -e "${RED}[build] ERROR:${NC} $*" >&2; exit 1; }

# ── Volume validation ─────────────────────────────────────────────────────────

if [[ ! -f "$CONFIG_FILE" ]]; then
    die "config.json not found at '${CONFIG_FILE}'.

Mount your config directory at /input:

  docker run --rm \\
    -v /absolute/path/to/config-dir:/input:ro \\
    -v /absolute/path/to/output:/output \\
    -v /absolute/path/to/keystores:/keystores \\
    webtoapp-builder

The /input directory must contain config.json.
Optionally place icon.png and splash.png inside /input/assets/."
fi

if [[ ! -d "$OUTPUT_DIR" ]]; then
    die "/output is not a directory. Mount a writable output volume."
fi

if [[ ! -d "$KEYSTORE_DIR" ]]; then
    warn "/keystores is not mounted — keystores will NOT persist between runs."
    warn "Play Store updates require the SAME signing key for every release."
    warn "Mount a persistent volume:  -v /path/to/keystores:/keystores"
    KEYSTORE_DIR=/tmp/keystores-$$
    mkdir -p "$KEYSTORE_DIR"
fi

# ── Resolve relative asset paths ──────────────────────────────────────────────
# iconPath / splashPath in config.json may be relative (e.g. "assets/icon.png").
# Rewrite them to absolute paths under /input so build_app.py can find them
# regardless of the working directory.

RESOLVED_CONFIG=/tmp/config-resolved-$$.json

log "Resolving config paths from ${CONFIG_FILE} …"

python3 - <<PYEOF
import json, sys
from pathlib import Path

cfg_path  = Path("${CONFIG_FILE}")
input_dir = Path("${INPUT_DIR}")
out_path  = Path("${RESOLVED_CONFIG}")

cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

for field in ("iconPath", "splashPath"):
    val = cfg.get(field, "")
    if not val:
        continue
    p = Path(val)
    if not p.is_absolute():
        p = input_dir / p
    if not p.exists():
        print(f"  WARNING: {field} not found at {p} — field removed from config",
              flush=True)
        cfg.pop(field, None)
    else:
        cfg[field] = str(p)
        print(f"  {field}: {p}", flush=True)

out_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
PYEOF

# ── Build ─────────────────────────────────────────────────────────────────────

BUILD_DIR="/workspace/build-$$"
START_TS=$(date +%s)

cleanup() {
    local code=$?
    rm -rf "$BUILD_DIR" "${RESOLVED_CONFIG}" 2>/dev/null || true
    if [[ $code -ne 0 ]]; then
        echo -e "${RED}[build] Build failed (exit ${code}).${NC}" >&2
        echo -e "${RED}[build] Tip: re-run with --verbose for full Gradle output.${NC}" >&2
    fi
}
trap cleanup EXIT

log "Starting build (template: ${TEMPLATE_DIR}) …"

python3 /app/build_app.py \
    --config       "$RESOLVED_CONFIG" \
    --template     "$TEMPLATE_DIR" \
    --output       "$OUTPUT_DIR" \
    --keystore-dir "$KEYSTORE_DIR" \
    --build-dir    "$BUILD_DIR" \
    "$@"

ELAPSED=$(( $(date +%s) - START_TS ))
echo -e "${GREEN}${BOLD}[build] Done in ${ELAPSED}s.${NC}"
log "Artefacts written to ${OUTPUT_DIR}:"
ls -lh "${OUTPUT_DIR}"/*.apk "${OUTPUT_DIR}"/*.aab 2>/dev/null \
    | awk '{print "  " $NF "  (" $5 ")"}'
