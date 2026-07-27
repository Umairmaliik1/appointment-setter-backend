#!/bin/bash
set -e

# Start FastAPI server in background
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips "*" &

# Start LiveKit voice worker in foreground (so container stays alive)
python run_voice_worker.py start

