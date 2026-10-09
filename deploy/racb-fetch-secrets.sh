#!/bin/sh
# Fetches the bot's .env contents from Google Secret Manager and writes them to a file.
#
# Used by deploy/racb.service on the VM (authenticated through the VM's service account), and usable
# anywhere else gcloud is authenticated, e.g. a Claude Code cloud session running integration tests.
# See deploy/SECRET_MANAGER.md.
#
# Usage:       racb-fetch-secrets.sh [OUTPUT_PATH]        (default: /run/racb/env)
# Environment: RACB_SECRET_NAME  secret to read           (default: racb-env)
#              RACB_GCP_PROJECT  project holding it       (default: VM metadata, then gcloud config)
set -eu

OUTPUT_PATH="${1:-/run/racb/env}"
SECRET_NAME="${RACB_SECRET_NAME:-racb-env}"

# systemd's default PATH lacks /snap/bin, where Ubuntu's GCE images install gcloud
PATH="$PATH:/snap/bin:/usr/lib/google-cloud-sdk/bin"
export PATH

PROJECT="${RACB_GCP_PROJECT:-}"
if [ -z "$PROJECT" ]; then
    PROJECT="$(curl -fsS --max-time 2 -H 'Metadata-Flavor: Google' \
        http://metadata.google.internal/computeMetadata/v1/project/project-id 2>/dev/null || true)"
fi
if [ -z "$PROJECT" ]; then
    PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [ -z "$PROJECT" ]; then
    echo "Cannot determine the GCP project; set RACB_GCP_PROJECT" >&2
    exit 1
fi

OUTPUT_DIR="$(dirname "$OUTPUT_PATH")"
umask 077
CREATED_OUTPUT_DIR=false
if [ ! -d "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR"
    CREATED_OUTPUT_DIR=true
fi

# Write to a temporary file and rename, so a failed fetch never leaves a truncated env file behind
TMP_PATH="$OUTPUT_PATH.tmp.$$"
trap 'rm -f "$TMP_PATH"' EXIT
gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$PROJECT" > "$TMP_PATH"

# Members of the docker group can already read any container's environment, so letting them read
# this file costs nothing and lets them run `docker compose` without sudo
if [ "$(id -u)" -eq 0 ] && getent group docker > /dev/null 2>&1; then
    chgrp docker "$TMP_PATH"
    chmod 0640 "$TMP_PATH"
    if [ "$CREATED_OUTPUT_DIR" = true ]; then
        chgrp docker "$OUTPUT_DIR"
        chmod 0750 "$OUTPUT_DIR"
    fi
fi

mv "$TMP_PATH" "$OUTPUT_PATH"
echo "Wrote secret '$SECRET_NAME' (project '$PROJECT') to $OUTPUT_PATH"
