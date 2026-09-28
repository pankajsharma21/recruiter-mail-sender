#!/usr/bin/env bash
# Stop the Recruiter Mail Sender.
# Usage: ./stop.sh
set -uo pipefail
cd "$(dirname "$0")"

SENDER='^[^ ]*/python[0-9.]* ([^ ]*/)?\.venv/bin/mailsend( |$)'
stopped=0

if [ -f .mailsend.pid ]; then
  pid="$(cat .mailsend.pid 2>/dev/null || true)"
  # A stale file can name a PID that now belongs to something else: check it first.
  if [ -n "${pid:-}" ] && ps -o args= -p "$pid" 2>/dev/null | grep -Eq "$SENDER" \
     && kill "$pid" 2>/dev/null; then stopped=1; fi
  rm -f .mailsend.pid
fi

for p in $(pgrep -f "$SENDER" 2>/dev/null || true); do
  kill "$p" 2>/dev/null && stopped=1 || true
done

# A send in progress finishes its current mail first (up to about a minute).
for _ in $(seq 1 120); do
  pgrep -f "$SENDER" >/dev/null 2>&1 || break
  sleep 1
done
if [ "$stopped" = 1 ]; then
  echo "Recruiter Mail Sender stopped."
else
  echo "Nothing was running."
fi
