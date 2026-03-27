#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTHELIA_DIR="${ROOT_DIR}/docker/authelia"
USERS_FILE="${AUTHELIA_DIR}/users_database.yml"

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/authelia_user_manage.sh list
  bash scripts/authelia_user_manage.sh add <username> [display_name] [email] [groups_csv]
  bash scripts/authelia_user_manage.sh rotate-password <username>
  bash scripts/authelia_user_manage.sh enable <username>
  bash scripts/authelia_user_manage.sh disable <username>
  bash scripts/authelia_user_manage.sh delete <username>

Notes:
- Passwords are read securely from stdin/TTY and never written to .env.
- This script edits docker/authelia/users_database.yml directly.
- Authelia is restarted automatically if its container is running.
USAGE
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

require_python_yaml() {
  python3 - <<'PY' >/dev/null 2>&1 || exit 1
import yaml  # noqa: F401
PY
}

ensure_authelia_dir_writable() {
  mkdir -p "${AUTHELIA_DIR}"
  if touch "${AUTHELIA_DIR}/.perm_check" 2>/dev/null; then
    rm -f "${AUTHELIA_DIR}/.perm_check"
    return 0
  fi

  if command -v sudo >/dev/null 2>&1; then
    echo "Authelia directory is not writable; repairing ownership with sudo..."
    sudo chown -R "$(id -u):$(id -g)" "${AUTHELIA_DIR}"
    touch "${AUTHELIA_DIR}/.perm_check"
    rm -f "${AUTHELIA_DIR}/.perm_check"
    return 0
  fi

  die "Authelia directory is not writable and sudo is unavailable: ${AUTHELIA_DIR}"
}

hash_password() {
  local password="$1"
  docker run --rm authelia/authelia:latest \
    authelia crypto hash generate argon2 --password "${password}" --no-confirm \
    | awk -F'Digest: ' '/Digest: / {print $2; exit}'
}

prompt_password() {
  local p1 p2
  read -rs -p "Password: " p1
  echo
  read -rs -p "Confirm password: " p2
  echo
  [[ -n "${p1}" ]] || die "Password cannot be empty"
  [[ "${p1}" == "${p2}" ]] || die "Passwords do not match"
  printf '%s' "${p1}"
}

ensure_users_file() {
  mkdir -p "${AUTHELIA_DIR}"
  if [[ ! -f "${USERS_FILE}" ]]; then
    cat > "${USERS_FILE}" <<'EOF_USERS'
users: {}
EOF_USERS
  fi
  chmod 700 "${AUTHELIA_DIR}" || true
}

python_edit_users() {
  local action="$1"
  local username="${2:-}"
  local display_name="${3:-}"
  local email="${4:-}"
  local groups_csv="${5:-}"
  local password_hash="${6:-}"

  USERS_FILE="${USERS_FILE}" \
  ACTION="${action}" \
  USERNAME="${username}" \
  DISPLAY_NAME="${display_name}" \
  EMAIL="${email}" \
  GROUPS_CSV="${groups_csv}" \
  PASSWORD_HASH="${password_hash}" \
  python3 - <<'PY'
import os
import shutil
import sys
import yaml

path = os.environ["USERS_FILE"]
action = os.environ["ACTION"]
username = os.environ.get("USERNAME", "")
display_name = os.environ.get("DISPLAY_NAME", "")
email = os.environ.get("EMAIL", "")
groups_csv = os.environ.get("GROUPS_CSV", "")
password_hash = os.environ.get("PASSWORD_HASH", "")

def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)

with open(path, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}

if not isinstance(data, dict):
    fail("users file is not a YAML mapping")

users = data.setdefault("users", {})
if not isinstance(users, dict):
    fail("users entry is not a mapping")

if action == "list":
    for name in sorted(users.keys()):
        entry = users.get(name) or {}
        disabled = bool(entry.get("disabled", False))
        groups = entry.get("groups", [])
        if not isinstance(groups, list):
            groups = []
        print(f"{name}\tdisabled={str(disabled).lower()}\tgroups={','.join(groups)}")
    raise SystemExit(0)

if not username:
    fail("username is required")

if action == "add":
    groups = [g.strip() for g in groups_csv.split(",") if g.strip()]
    if not groups:
      groups = ["admins"]
    users[username] = {
        "disabled": False,
        "displayname": display_name or username,
        "password": password_hash,
        "email": email or f"{username}@local",
        "groups": groups,
    }
elif action == "rotate-password":
    if username not in users:
        fail(f"user '{username}' not found")
    users[username]["password"] = password_hash
elif action == "enable":
    if username not in users:
        fail(f"user '{username}' not found")
    users[username]["disabled"] = False
elif action == "disable":
    if username not in users:
        fail(f"user '{username}' not found")
    users[username]["disabled"] = True
elif action == "delete":
    if username not in users:
        fail(f"user '{username}' not found")
    users.pop(username)
else:
    fail(f"unknown action: {action}")

backup = f"{path}.bak"
shutil.copy2(path, backup)

with open(path, "w", encoding="utf-8") as f:
    yaml.safe_dump(
        data,
        f,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=False,
    )
PY
}

restart_authelia_if_running() {
  if docker compose -f "${ROOT_DIR}/docker-compose.yml" -f "${ROOT_DIR}/docker-compose.zimaboard.yml" ps --status running --services 2>/dev/null | grep -qx authelia; then
    docker compose -f "${ROOT_DIR}/docker-compose.yml" -f "${ROOT_DIR}/docker-compose.zimaboard.yml" restart authelia >/dev/null
    echo "Authelia restarted to apply user change."
  fi
}

main() {
  local cmd="${1:-}"
  local username="${2:-}"
  local needs_restart=0

  [[ -n "${cmd}" ]] || { usage; exit 1; }

  require_cmd docker
  require_cmd python3
  require_python_yaml || die "python3 'yaml' module is required (install package: python3-yaml)"

  ensure_authelia_dir_writable
  ensure_users_file

  case "${cmd}" in
    list)
      python_edit_users list
      ;;
    add)
      [[ -n "${username}" ]] || die "Username is required"
      local display_name="${3:-${username}}"
      local email="${4:-${username}@local}"
      local groups_csv="${5:-admins}"
      local password password_hash
      password="$(prompt_password)"
      password_hash="$(hash_password "${password}")"
      [[ -n "${password_hash}" ]] || die "Failed to generate password hash"
      python_edit_users add "${username}" "${display_name}" "${email}" "${groups_csv}" "${password_hash}"
      echo "User added/updated: ${username}"
      needs_restart=1
      ;;
    rotate-password)
      [[ -n "${username}" ]] || die "Username is required"
      local password password_hash
      password="$(prompt_password)"
      password_hash="$(hash_password "${password}")"
      [[ -n "${password_hash}" ]] || die "Failed to generate password hash"
      python_edit_users rotate-password "${username}" "" "" "" "${password_hash}"
      echo "Password rotated: ${username}"
      needs_restart=1
      ;;
    enable)
      [[ -n "${username}" ]] || die "Username is required"
      python_edit_users enable "${username}"
      echo "User enabled: ${username}"
      needs_restart=1
      ;;
    disable)
      [[ -n "${username}" ]] || die "Username is required"
      python_edit_users disable "${username}"
      echo "User disabled: ${username}"
      needs_restart=1
      ;;
    delete)
      [[ -n "${username}" ]] || die "Username is required"
      python_edit_users delete "${username}"
      echo "User deleted: ${username}"
      needs_restart=1
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage
      die "Unknown command: ${cmd}"
      ;;
  esac

  chmod 600 "${USERS_FILE}" || true
  if (( needs_restart == 1 )); then
    restart_authelia_if_running
  fi
}

main "$@"
