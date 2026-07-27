# PostgreSQL Migration Runbook (Backend)

## Current State
- PostgreSQL foundation is added (`DATABASE_URL`, SQLAlchemy engine, Alembic scaffolding, Postgres health checks).
- Core and domain tables are defined with Alembic revisions:
  - `20260427_0001` (auth/org/platform/audit/idempotency)
  - `20260427_0002` (tenant/telephony/chatbot/provisioning/contact domains)
- Backend runtime is fully PostgreSQL-backed.
- Legacy cloud database SDK dependencies are removed from backend requirements.
- Cold-caller repository remains Firebase-backed by decision.

## Backup and Restore
- Backup: `scripts/postgres_backup.sh`
- Restore: `scripts/postgres_restore.sh <backup.sql.gz>`

## Data Migration Utility
- Script: `scripts/migrate_legacy_json_to_postgres.py`
- Expected input folder:
  - `users.json|jsonl|ndjson`
  - `orgs.json|jsonl|ndjson`
  - `org_memberships.json|jsonl|ndjson`
  - `partner_entitlements.json|jsonl|ndjson`
  - `platform_roles.json|jsonl|ndjson`
  - `audit_logs.json|jsonl|ndjson`
  - `idempotency_keys.json|jsonl|ndjson`
  - `tenants.json|jsonl|ndjson`
  - `business_configs.json|jsonl|ndjson`
  - `agent_settings.json|jsonl|ndjson`
  - `twilio_integrations.json|jsonl|ndjson`
  - `agents.json|jsonl|ndjson`
  - `phone_numbers.json|jsonl|ndjson`
  - `appointments.json|jsonl|ndjson`
  - `provisioning_jobs.json|jsonl|ndjson`
  - `contacts.json|jsonl|ndjson`
  - `chatbot_agents.json|jsonl|ndjson`
  - `chatbot_runtime_logs.json|jsonl|ndjson`
  - `system_settings.json|jsonl|ndjson`
  - `chatbot_chat_sessions.json|jsonl|ndjson`
  - `chatbot_chat_messages.json|jsonl|ndjson`
- Dry run:
  - `python scripts/migrate_legacy_json_to_postgres.py --input-dir <export_dir> --dry-run`
- Apply:
  - `python scripts/migrate_legacy_json_to_postgres.py --input-dir <export_dir> --apply --report-file migration-reports/legacy_to_postgres_report.json`

## VPS Deployment Notes
1. Set `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.
2. Set backend `DATABASE_URL`.
3. Start `postgres`, `redis`, `backend`, `voice-worker` via docker compose.
4. Run Alembic migrations before switching traffic:
   - `alembic upgrade 20260427_0001`
   - `alembic upgrade 20260427_0002`
5. Import legacy Firestore data into PostgreSQL before enabling user traffic.

## Cutover Checklist
1. Planned downtime start.
2. Apply Alembic migrations.
3. Run migration script in `--dry-run` and resolve any skipped/invalid records.
4. Run migration script in `--apply` mode and review generated reconciliation report.
5. Validate critical paths:
   - `/api/v1/auth/login`, `/api/v1/auth/me`
   - `/api/v1/platform/bootstrap`
   - org/partner management endpoints
   - chatbot embed/live chat websocket flows
6. Run smoke tests (`/health`, bootstrap, live flows, telephony lookup).
7. Resume traffic.
