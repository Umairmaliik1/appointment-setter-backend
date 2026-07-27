#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${BACKEND_CONTAINER:-appointment-setter-backend}"

usage() {
  cat <<'EOF'
Create an active application user with role "user" inside the running backend container.

Usage:
  scripts/create_role_user.sh \
    --email user@example.com \
    --username user123 \
    --first-name First \
    --last-name Last

Options:
  --email         Required. Login email.
  --username      Required. Unique username, 3-72 chars.
  --first-name    Required.
  --last-name     Required.
  --password      Optional. If omitted, you will be prompted securely.
  --tenant-id     Optional. UUID tenant id if this user should be tied to a tenant.
  --container     Optional. Backend container name. Defaults to appointment-setter-backend.

Password must be 8-72 chars and include uppercase, lowercase, and a digit.
EOF
}

EMAIL=""
USERNAME=""
FIRST_NAME=""
LAST_NAME=""
PASSWORD=""
TENANT_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --email)
      EMAIL="${2:-}"
      shift 2
      ;;
    --username)
      USERNAME="${2:-}"
      shift 2
      ;;
    --first-name)
      FIRST_NAME="${2:-}"
      shift 2
      ;;
    --last-name)
      LAST_NAME="${2:-}"
      shift 2
      ;;
    --password)
      PASSWORD="${2:-}"
      shift 2
      ;;
    --tenant-id)
      TENANT_ID="${2:-}"
      shift 2
      ;;
    --container)
      CONTAINER="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$EMAIL" || -z "$USERNAME" || -z "$FIRST_NAME" || -z "$LAST_NAME" ]]; then
  echo "Missing required argument." >&2
  usage >&2
  exit 1
fi

if [[ -z "$PASSWORD" ]]; then
  read -r -s -p "Password: " PASSWORD
  echo
  read -r -s -p "Confirm password: " PASSWORD_CONFIRM
  echo
  if [[ "$PASSWORD" != "$PASSWORD_CONFIRM" ]]; then
    echo "Passwords do not match." >&2
    exit 1
  fi
fi

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" >/dev/null 2>&1; then
  echo "Container '$CONTAINER' is not running or does not exist." >&2
  echo "Set BACKEND_CONTAINER or pass --container if your backend container has another name." >&2
  exit 1
fi

docker exec -i \
  -e CREATE_USER_EMAIL="$EMAIL" \
  -e CREATE_USER_USERNAME="$USERNAME" \
  -e CREATE_USER_FIRST_NAME="$FIRST_NAME" \
  -e CREATE_USER_LAST_NAME="$LAST_NAME" \
  -e CREATE_USER_PASSWORD="$PASSWORD" \
  -e CREATE_USER_TENANT_ID="$TENANT_ID" \
  "$CONTAINER" \
  python - <<'PY'
import asyncio
import os
import sys

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.api.v1.schemas.auth import UserCreate
from app.api.v1.services.auth import auth_service
from app.models.auth import UserRole


async def main() -> int:
    email = os.environ["CREATE_USER_EMAIL"].strip().lower()
    username = os.environ["CREATE_USER_USERNAME"].strip()
    first_name = os.environ["CREATE_USER_FIRST_NAME"].strip()
    last_name = os.environ["CREATE_USER_LAST_NAME"].strip()
    password = os.environ["CREATE_USER_PASSWORD"]
    tenant_id = os.environ.get("CREATE_USER_TENANT_ID", "").strip() or None

    existing = await auth_service.get_user_by_email(email)
    if existing:
        print(f"User already exists for email: {email}", file=sys.stderr)
        return 2

    try:
        payload = UserCreate(
            email=email,
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.USER,
            tenant_id=tenant_id,
        )
        user = await auth_service.create_user(payload)
    except ValidationError as exc:
        print(exc, file=sys.stderr)
        return 1
    except IntegrityError as exc:
        print("Could not create user because email or username is already in use.", file=sys.stderr)
        print(str(exc.orig), file=sys.stderr)
        return 2

    print("Created user")
    print(f"  id: {user['id']}")
    print(f"  email: {user['email']}")
    print(f"  username: {user['username']}")
    print(f"  role: {user['role']}")
    print(f"  status: {user['status']}")
    print(f"  allowed_app_ids: {', '.join(user.get('allowed_app_ids') or [])}")
    return 0


raise SystemExit(asyncio.run(main()))
PY
