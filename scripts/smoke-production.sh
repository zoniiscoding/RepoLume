#!/bin/sh
set -eu

: "${PRODUCTION_API_ORIGIN:?PRODUCTION_API_ORIGIN is required}"
: "${PRODUCTION_FRONTEND_ORIGIN:?PRODUCTION_FRONTEND_ORIGIN is required}"

case "$PRODUCTION_API_ORIGIN" in
  https://*/*) echo "PRODUCTION_API_ORIGIN must not contain a path" >&2; exit 2 ;;
  https://*) ;;
  *) echo "PRODUCTION_API_ORIGIN must use HTTPS" >&2; exit 2 ;;
esac
case "$PRODUCTION_FRONTEND_ORIGIN" in
  https://*/*) echo "PRODUCTION_FRONTEND_ORIGIN must not contain a path" >&2; exit 2 ;;
  https://*) ;;
  *) echo "PRODUCTION_FRONTEND_ORIGIN must use HTTPS" >&2; exit 2 ;;
esac

temporary_directory=$(mktemp -d)
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM

curl --fail --silent --show-error \
  --dump-header "$temporary_directory/api-headers" \
  --output "$temporary_directory/live-body" \
  "$PRODUCTION_API_ORIGIN/api/v1/health/live"
grep --fixed-strings '"status":"ok"' "$temporary_directory/live-body" >/dev/null
grep -i '^strict-transport-security:' "$temporary_directory/api-headers" >/dev/null
grep -i '^x-content-type-options: nosniff' "$temporary_directory/api-headers" >/dev/null

curl --fail --silent --show-error \
  --output "$temporary_directory/ready-body" \
  "$PRODUCTION_API_ORIGIN/api/v1/health/ready"
grep --fixed-strings '"status":"ready"' "$temporary_directory/ready-body" >/dev/null

curl --fail --silent --show-error \
  --dump-header "$temporary_directory/frontend-headers" \
  --output /dev/null \
  "$PRODUCTION_FRONTEND_ORIGIN/auth/callback"
grep -i '^strict-transport-security:' "$temporary_directory/frontend-headers" >/dev/null
grep -i '^x-frame-options: DENY' "$temporary_directory/frontend-headers" >/dev/null
grep -i '^content-security-policy:' "$temporary_directory/frontend-headers" \
  | grep --fixed-strings "connect-src 'self' $PRODUCTION_API_ORIGIN" >/dev/null

webhook_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'X-GitHub-Event: ping' \
  --header 'X-GitHub-Delivery: 00000000-0000-4000-8000-000000000000' \
  --header 'X-Hub-Signature-256: sha256=0000000000000000000000000000000000000000000000000000000000000000' \
  --data '{}' \
  "$PRODUCTION_API_ORIGIN/api/v1/webhooks/github")
test "$webhook_status" = "401"

curl --silent --show-error \
  --request OPTIONS \
  --header 'Origin: https://untrusted.example.invalid' \
  --header 'Access-Control-Request-Method: POST' \
  --dump-header "$temporary_directory/cors-headers" \
  --output /dev/null \
  "$PRODUCTION_API_ORIGIN/api/v1/auth/refresh"
if grep -i '^access-control-allow-origin:' "$temporary_directory/cors-headers" >/dev/null; then
  echo "Untrusted origin received an Access-Control-Allow-Origin response" >&2
  exit 1
fi

echo "Production unauthenticated smoke checks passed"
