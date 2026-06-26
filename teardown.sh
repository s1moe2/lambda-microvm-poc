#!/usr/bin/env bash
# Removes everything setup.sh created. Best-effort + idempotent.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$DIR/.poc-state"
# shellcheck disable=SC1090
[ -f "$STATE_FILE" ] && source "$STATE_FILE"
REGION="${REGION:-us-east-1}"
: "${BUCKET:?no state found; set BUCKET=... or run setup.sh first}"
echo "tearing down (region=$REGION bucket=$BUCKET)..."

if [ -n "${MVM:-}" ]; then
  aws lambda-microvms terminate-microvm --region "$REGION" --microvm-identifier "$MVM" 2>/dev/null \
    && echo "terminated $MVM" || echo "microvm gone/absent"
  sleep 5
fi

if [ -n "${IMAGE_NAME:-}" ]; then
  aws lambda-microvms delete-microvm-image --region "$REGION" --image-identifier "$IMAGE_NAME" 2>/dev/null \
    && echo "deleted image $IMAGE_NAME" || echo "image delete skipped (already gone?)"
fi

if [ -n "${ROLE_NAME:-}" ]; then
  aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name build-perms 2>/dev/null || true
  aws iam delete-role --role-name "$ROLE_NAME" 2>/dev/null && echo "deleted role $ROLE_NAME" || echo "role delete skipped"
fi

aws s3 rm "s3://${BUCKET}" --recursive 2>/dev/null || true
aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null \
  && echo "deleted bucket $BUCKET" || echo "bucket delete skipped"

rm -f "$DIR/probe.zip" "$STATE_FILE"
echo "teardown complete."
