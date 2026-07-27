"""
LiveKit Voice Agent Worker
==========================
Starts the voice agent worker that handles inbound telephony calls.

ARCHITECTURE (LiveKit Official Telephony Flow - Multi-Tenant Design):
- ONE worker with agent_name="voice-agent" handles ALL phone numbers
- Each phone number has its own dispatch rule pointing to "voice-agent"
- Worker receives jobs when LiveKit creates rooms via dispatch rule
- Worker extracts tenant_id, call_id, and called_number from SIP headers
- Worker loads config from Redis: tenant_config:<tenant_id>:<call_id>
- Worker FAILS if config is missing (no fallback to defaults)
"""

import os
import sys
import multiprocessing
import logging
from dotenv import load_dotenv

# -------------------------------------------------
# CRITICAL: Optimize ONNX / PyTorch threading
# -------------------------------------------------
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

# Load environment variables
load_dotenv()

from livekit import agents
from app.agents.voice_worker import entrypoint
from app.core.config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL

AGENT_NAME = "voice-agent"

# -------------------------------------------------
# Production worker tuning (LiveKit official defaults)
#   https://docs.livekit.io/agents/server/options/
#   https://docs.livekit.io/deploy/custom/deployments/
#
# All values are env-overridable so the same image can run on
# differently-sized hosts without code changes.
# -------------------------------------------------
# LiveKit's documented production default is 4 warm processes
# (dev default is 0). Keep ALL phone numbers covered by at least a
# couple of warm processes so an inbound call never cold-starts.
NUM_IDLE_PROCESSES = int(os.environ.get("LIVEKIT_WORKER_IDLE_PROCESSES", "2"))

# Max time a job/inference process may take to initialize (prewarm VAD,
# etc.) before the supervisor declares it dead. Default in the SDK is 10s.
# We bound it explicitly so a starved/stuck prewarm FAILS FAST and the
# process is recycled — instead of hanging the whole worker for ~10 min
# (the symptom seen in production on 2026-04-28).
INIT_PROCESS_TIMEOUT = float(os.environ.get("LIVEKIT_INIT_PROCESS_TIMEOUT", "30"))

# Load above which the worker stops accepting new jobs ("unavailable").
# SDK default is 0.7 (overall CPU utilization). Override per host size.
LOAD_THRESHOLD = float(os.environ.get("LIVEKIT_LOAD_THRESHOLD", "0.75"))

# Per-job memory guardrails (MB). 0 disables. Warn before killing so we
# get a log breadcrumb instead of a silent OOM.
JOB_MEMORY_WARN_MB = float(os.environ.get("LIVEKIT_JOB_MEMORY_WARN_MB", "1500"))
JOB_MEMORY_LIMIT_MB = float(os.environ.get("LIVEKIT_JOB_MEMORY_LIMIT_MB", "0"))

# Time to let in-flight calls finish on SIGTERM/SIGINT (graceful drain).
DRAIN_TIMEOUT = float(os.environ.get("LIVEKIT_DRAIN_TIMEOUT", "120"))


if __name__ == "__main__":
    multiprocessing.freeze_support()

    print("=" * 70)
    print("Starting LiveKit Voice Agent Worker")
    print("=" * 70)
    print(f"LiveKit URL: {LIVEKIT_URL}")
    print(f"Agent Name: {AGENT_NAME}")
    print(f"  ↳ Single global agent handles ALL phone numbers")
    print(f"  ↳ Multi-tenant routing via SIP headers (X-LK-TenantId)")
    print("=" * 70)

    # -------------------------------------------------
    # Windows IPC warning suppressor
    # -------------------------------------------------
    if sys.platform == "win32":
        class WindowsIPCFilter(logging.Filter):
            def filter(self, record):
                msg = record.getMessage()
                return not ("DuplexClosed" in msg or "Error in _read_ipc_task" in msg)

        logging.getLogger("livekit.agents").addFilter(WindowsIPCFilter())
        print("NOTE: Running on Windows — IPC watcher warnings suppressed.\n")

    try:
        # -------------------------------------------------
        # Ensure env vars are available for LiveKit SDK
        # -------------------------------------------------
        if LIVEKIT_URL:
            os.environ["LIVEKIT_URL"] = LIVEKIT_URL
        if LIVEKIT_API_KEY:
            os.environ["LIVEKIT_API_KEY"] = LIVEKIT_API_KEY
        if LIVEKIT_API_SECRET:
            os.environ["LIVEKIT_API_SECRET"] = LIVEKIT_API_SECRET

        print("[WORKER CONFIG]")
        print(f"  LIVEKIT_URL set: {'yes' if LIVEKIT_URL else 'no'}")
        print(f"  LIVEKIT_API_KEY set: {'yes' if LIVEKIT_API_KEY else 'no'}")
        print(f"  LIVEKIT_API_SECRET set: {'yes' if LIVEKIT_API_SECRET else 'no'}")
        print(f"  Agent Name: {AGENT_NAME}")
        print(f"  num_idle_processes: {NUM_IDLE_PROCESSES}")
        print(f"  initialize_process_timeout: {INIT_PROCESS_TIMEOUT}s")
        print(f"  load_threshold: {LOAD_THRESHOLD}")
        print(f"  job_memory_warn_mb: {JOB_MEMORY_WARN_MB}")
        print(f"  job_memory_limit_mb: {JOB_MEMORY_LIMIT_MB}")
        print(f"  drain_timeout: {DRAIN_TIMEOUT}s")
        print("=" * 70)

        # -------------------------------------------------
        # Start LiveKit worker
        # -------------------------------------------------
        agents.cli.run_app(
            agents.WorkerOptions(
                entrypoint_fnc=entrypoint,
                api_key=LIVEKIT_API_KEY,
                api_secret=LIVEKIT_API_SECRET,
                ws_url=LIVEKIT_URL,
                agent_name=AGENT_NAME,
                num_idle_processes=NUM_IDLE_PROCESSES,
                # Fail fast on a stuck prewarm instead of hanging ~10 min.
                initialize_process_timeout=INIT_PROCESS_TIMEOUT,
                load_threshold=LOAD_THRESHOLD,
                job_memory_warn_mb=JOB_MEMORY_WARN_MB,
                job_memory_limit_mb=JOB_MEMORY_LIMIT_MB,
                drain_timeout=DRAIN_TIMEOUT,
            )
        )

    except KeyboardInterrupt:
        print("\n✓ Worker stopped by user.")
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ FATAL ERROR IN WORKER: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
