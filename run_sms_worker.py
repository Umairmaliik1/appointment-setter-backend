"""SMS drip worker launcher.

Polls the ``scheduled_sends`` outbox and dispatches due cold-SMS messages
through each tenant's Twilio credentials. Safe to scale horizontally
(``docker compose up -d --scale sms-worker=3``) — claiming uses
``FOR UPDATE SKIP LOCKED`` so workers never grab the same row.

Usage:
    python run_sms_worker.py start
"""

import sys

from dotenv import load_dotenv

load_dotenv()

from app.agents.sms_worker import main  # noqa: E402  (after load_dotenv)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "start"
    if arg != "start":
        print(f"Unknown command: {arg}. Usage: python run_sms_worker.py start")
        sys.exit(2)

    print("=" * 70)
    print("Starting SMS Drip Worker")
    print("=" * 70)
    main()
