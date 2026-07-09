#!/bin/bash
set -euo pipefail

app_user="reader"
app_group="reader"

first_arg="${1:-}"

ACTION_OVERRIDE=""

if [ -n "$first_arg" ]; then
    if [[ "$first_arg" == --action=* ]]; then
        ACTION_OVERRIDE="${first_arg#--action=}"
        if [ -z "$ACTION_OVERRIDE" ]; then
            echo "error: --action requires a value" >&2
            exit 1
        fi
    fi
fi

LOOP_SECONDS="${LOOP_SECONDS:-3600}"

if [ -n "$ACTION_OVERRIDE" ]; then
    ACTION="$ACTION_OVERRIDE"
    if [ "$ACTION" = "sync" ]; then
        LOOP_SECONDS=0
    fi
else
    ACTION="${ACTION:-display}"
fi

file_env() {
    local var_name="$1"
    local default_value="${2:-}"
    local file_var_name="${var_name}_FILE"
    local var_value="${!var_name:-}"
    local file_value="${!file_var_name:-}"

    if [ -n "$var_value" ] && [ -n "$file_value" ]; then
        echo "error: both ${var_name} and ${file_var_name} are set (use only one)" >&2
        exit 1
    fi

    if [ -n "$var_value" ]; then
        export "$var_name=$var_value"
        return
    fi

    if [ -n "$file_value" ]; then
        if [ ! -r "$file_value" ]; then
            echo "error: ${file_var_name} points to unreadable file '$file_value'" >&2
            exit 1
        fi
        local secret_value
        secret_value="$(<"$file_value")"
        export "$var_name=$secret_value"
        unset "$file_var_name"
        return
    fi

    export "$var_name=$default_value"
}

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
USER_SHELL="${USER_SHELL:-/usr/sbin/nologin}"
LOG_LEVEL="${LOG_LEVEL:-ERROR}"
UNIFI_KEEP_FILE="${UNIFI_KEEP_FILE:-}"

file_env "UNIFI_API_KEY"
file_env "UNIFI_HOST"
file_env "TRAEFIK_IP"
file_env "TRAEFIK_DNS"
file_env "TRAEFIK_HOST"
file_env "TRAEFIK_PORT" "8080"
file_env "TRAEFIK_PATH" "/api/http/routers"

UNIFI_API_KEY="${UNIFI_API_KEY:-}"
UNIFI_HOST="${UNIFI_HOST:-}"
TRAEFIK_IP="${TRAEFIK_IP:-}"
TRAEFIK_DNS="${TRAEFIK_DNS:-}"
TRAEFIK_HOST="${TRAEFIK_HOST:-$TRAEFIK_IP}"
TRAEFIK_PORT="${TRAEFIK_PORT:-8080}"
TRAEFIK_PATH="${TRAEFIK_PATH:-/api/http/routers}"

if ! [[ "$PUID" =~ ^[0-9]+$ && "$PGID" =~ ^[0-9]+$ ]]; then
    echo "error: PUID and PGID must be numeric" >&2
    exit 1
fi

if [ "$PUID" = "0" ]; then
    echo "error: PUID=0 is not allowed" >&2
    exit 1
fi

if [ "$(id -u)" != "0" ]; then
    echo "error: entrypoint must start as root to map runtime user" >&2
    exit 1
fi

shellNologin="/usr/sbin/nologin"

if [ -n "${USER_SHELL:-}" ]; then
    case "$USER_SHELL" in
        /*) user_shell="$USER_SHELL" ;;
        *) user_shell="/usr/bin/$USER_SHELL" ;;
    esac
else
    user_shell="$shellNologin"
fi

if [ "$USER_SHELL" != "$shellNologin" ] && { [ ! -f /etc/shells ] || ! grep -q "^${user_shell}$" /etc/shells; }; then
    echo "error: USER_SHELL '$user_shell' is not present in /etc/shells" >&2
    exit 1
fi

primary_group_name="$(getent group "$PGID" | cut -d: -f1 || true)"
if [ -z "$primary_group_name" ]; then
    if getent group "$app_group" >/dev/null 2>&1; then
        groupmod -g "$PGID" "$app_group"
    else
        groupadd -g "$PGID" "$app_group"
    fi
fi

run_user=""
uid_owner="$(getent passwd "$PUID" | cut -d: -f1 || true)"
if [ -n "$uid_owner" ]; then
    run_user="$uid_owner"
    current_gid="$(id -g "$run_user")"
    if [ "$current_gid" != "$PGID" ]; then
        usermod -g "$PGID" "$run_user"
    fi
else
    if id "$app_user" >/dev/null 2>&1; then
        current_uid="$(id -u "$app_user")"
        if [ "$current_uid" != "$PUID" ]; then
            usermod -u "$PUID" "$app_user"
        fi
        current_gid="$(id -g "$app_user")"
        if [ "$current_gid" != "$PGID" ]; then
            usermod -g "$PGID" "$app_user"
        fi
        usermod -s "$user_shell" "$app_user"
        run_user="$app_user"
    else
        useradd --no-log-init -u "$PUID" -g "$PGID" -m -s "$user_shell" "$app_user" 2> /dev/null
        run_user="$app_user"
    fi
fi

# Ensure /app is owned by the runtime user and is readable
chown -R "$PUID:$PGID" /app || true
if [ ! -r /app ]; then
    echo "error: /app is not readable by $run_user (uid: $PUID, gid: $PGID)" >&2
    exit 1
fi

# Ensure /data exists and is writable by the runtime user
if [ ! -d /data ]; then
    mkdir -p /data
fi
chown -R "$PUID:$PGID" /data 2> /dev/null || true
if [ ! -w /data ]; then
    echo "error: /data is not writable by $run_user (uid: $PUID, gid: $PGID)" >&2
    exit 1
fi


ARGS=(
    "--action" "$ACTION"
    "--log-level" "$LOG_LEVEL"
)

if [ -n "$UNIFI_KEEP_FILE" ]; then
    ARGS+=("--unifi-keep-file" "$UNIFI_KEEP_FILE")
fi

run_once() {
  gosu "$run_user" /app/traefik_hosts.py "${ARGS[@]}"
}

if [ "$ACTION" = "sync" ]; then
    if ! [[ "$LOOP_SECONDS" =~ ^[0-9]+$ ]] || [ "$LOOP_SECONDS" -lt 0 ]; then
        echo "warning: ACTION=sync requires LOOP_SECONDS > 0; defaulting to 3600" >&2
        LOOP_SECONDS=3600
    fi

    consecutive_failures=0
    max_consecutive_failures=3

    if [ "$LOOP_SECONDS" -eq 0 ]; then
        echo "info: LOOP_SECONDS=0; running once and then exiting" 
        run_once
    else
        while true; do
            if run_once; then
                consecutive_failures=0
            else
                consecutive_failures=$((consecutive_failures + 1))
                echo "error: sync run failed (${consecutive_failures} consecutive failures)" >&2
                if [ "$consecutive_failures" -gt "$max_consecutive_failures" ]; then
                    echo "error: exceeded ${max_consecutive_failures} consecutive failures; exiting" >&2
                    exit 1
                fi
            fi

            sleep "$LOOP_SECONDS"
        done
    fi
else
    run_once
fi
