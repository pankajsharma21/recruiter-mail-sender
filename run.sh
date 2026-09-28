#!/usr/bin/env bash
# Start the Recruiter Mail Sender screen in the background and open it in the browser.
# Usage: ./run.sh            (./run.sh --port 9000 or --no-browser also work)
set -euo pipefail
cd "$(dirname "$0")"

# Match the real process only: a looser pattern also matches any shell or editor
# whose command line happens to contain the same text.
SENDER='^[^ ]*/python[0-9.]* ([^ ]*/)?\.venv/bin/mailsend( |$)'

if [ ! -x .venv/bin/mailsend ]; then
  echo "Not installed yet. Set it up first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

if pgrep -f "$SENDER" >/dev/null 2>&1; then
  echo "Already running. Look for the open tab, or stop it with ./stop.sh"
  exit 0
fi

mkdir -p logs
nohup .venv/bin/mailsend ui "$@" > logs/mailsend.log 2>&1 &
echo $! > .mailsend.pid

for _ in $(seq 1 20); do
  grep -q "open at" logs/mailsend.log 2>/dev/null && break
  sleep 0.3
done
echo "Recruiter Mail Sender started (PID $(cat .mailsend.pid))."
grep -o "http://[^ ]*" logs/mailsend.log | head -1 | sed 's/^/Screen: /'
echo "Log:  logs/mailsend.log"
echo "Stop: ./stop.sh   (a send in progress stops after the current mail)"
