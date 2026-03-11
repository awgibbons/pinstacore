#!/bin/bash

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS_FILE="${REPO_DIR}/update_status.json"
LOG_FILE="${REPO_DIR}/update_status.log"
SERVICE_NAME="instacore-web.service"

write_status() {
    local state="$1"
    local message="$2"
    local details=""

    if [ -f "$LOG_FILE" ]; then
        details=$(tail -n 40 "$LOG_FILE" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))")
    else
        details='""'
    fi

    cat > "$STATUS_FILE" <<EOF
{
  "state": "$state",
  "message": $(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$message"),
  "updated_at": $(date +%s),
  "details": $details
}
EOF
}

fail_update() {
    local message="$1"
    write_status "failed" "$message"
    exit 1
}

: > "$LOG_FILE"
write_status "running" "Starting software update..."

cd "$REPO_DIR" || fail_update "Could not access repo directory."

echo "Configuring Git safe.directory for updater..." >> "$LOG_FILE"
git config --global --add safe.directory "$REPO_DIR" >> "$LOG_FILE" 2>&1 || fail_update "Failed to configure git safe.directory."

echo "Fetching latest changes..." >> "$LOG_FILE"
git fetch --prune >> "$LOG_FILE" 2>&1 || fail_update "git fetch failed."

OLD_REV=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "Pulling latest changes..." >> "$LOG_FILE"
git pull --ff-only >> "$LOG_FILE" 2>&1 || fail_update "git pull --ff-only failed."
NEW_REV=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

write_status "restarting" "Updated ${OLD_REV} -> ${NEW_REV}. Restarting web service..."
sleep 2

systemctl restart "$SERVICE_NAME" >> "$LOG_FILE" 2>&1 || fail_update "Service restart failed."

write_status "complete" "Update complete. Running revision ${NEW_REV}."
exit 0