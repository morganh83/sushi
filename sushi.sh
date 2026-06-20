#!/data/data/com.termux/files/usr/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"

# ── Auto-update from git ──────────────────────────────────────────────────
do_update=true
for arg in "$@"; do [ "$arg" = "--no-update" ] && do_update=false; done

if $do_update && command -v git &>/dev/null && git -C "$DIR" rev-parse --git-dir &>/dev/null 2>&1; then
  REMOTE=$(git -C "$DIR" remote 2>/dev/null | head -1)
  if [ -n "$REMOTE" ]; then
    echo "[sushi] Checking for updates..."
    git -C "$DIR" fetch --quiet "$REMOTE" 2>/dev/null || true
    BRANCH=$(git -C "$DIR" rev-parse --abbrev-ref HEAD)
    BEHIND=$(git -C "$DIR" rev-list --count "HEAD..${REMOTE}/${BRANCH}" 2>/dev/null || echo 0)

    if [ "$BEHIND" -gt 0 ]; then
      echo "[sushi] $BEHIND new commit(s) — updating..."
      REQ_BEFORE=$(git -C "$DIR" rev-parse "HEAD:requirements.txt" 2>/dev/null || echo "none")
      git -C "$DIR" pull --ff-only --quiet "$REMOTE" "$BRANCH"
      echo "[sushi] Updated:"
      git -C "$DIR" log --oneline "HEAD@{1}..HEAD"

      REQ_AFTER=$(git -C "$DIR" rev-parse "HEAD:requirements.txt" 2>/dev/null || echo "none")
      if [ "$REQ_BEFORE" != "$REQ_AFTER" ] && [ -d "$VENV" ]; then
        echo "[sushi] Dependencies changed — reinstalling..."
        "$VENV/bin/pip" install --quiet -r "$DIR/requirements.txt"
      fi

      # Clear stale compiled bytecode so Python runs the updated source files
      find "$DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
    else
      echo "[sushi] Up to date."
    fi
  fi
fi

# ── Bootstrap venv on first run ───────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  echo "[sushi] Creating isolated environment..."
  python -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$DIR/requirements.txt"
  echo "[sushi] Ready."
fi

# ── Clear any leftover bytecode cache before launching ───────────────────
find "$DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# ── Launch ────────────────────────────────────────────────────────────────
ARGS=()
for arg in "$@"; do [ "$arg" != "--no-update" ] && ARGS+=("$arg"); done
exec "$VENV/bin/python" "$DIR/main.py" "${ARGS[@]}"
