#!/usr/bin/env bash
# Stands up the MicroVM NET_RAW probe end-to-end and prints the result.
# Creates: an S3 bucket, an IAM build role, a MicroVM image, and a running MicroVM.
# Run ./teardown.sh afterwards to remove everything.
set -euo pipefail

REGION="${REGION:-us-east-1}"          # must be a MicroVM-supported region
IMAGE_NAME="${IMAGE_NAME:-netraw-probe}"
ROLE_NAME="${ROLE_NAME:-MicrovmNetrawPocBuildRole}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$DIR/.poc-state"

# Folder holding the Dockerfile + app to deploy. Default: repo root (the simple probe).
#   SRC_DIR=dind IMAGE_NAME=dind-probe ./setup.sh   # the Docker-in-Docker demo
SRC_DIR="${SRC_DIR:-.}"
case "$SRC_DIR" in /*) ;; *) SRC_DIR="$DIR/$SRC_DIR" ;; esac
SRC_DIR="$(cd "$SRC_DIR" && pwd)"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-microvm-netraw-poc-${ACCOUNT}-${REGION}}"
echo "region=$REGION account=$ACCOUNT bucket=$BUCKET image=$IMAGE_NAME role=$ROLE_NAME src=$SRC_DIR"

# --- 1. S3 bucket ---
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
fi

# --- 2. IAM build role (trust Lambda; allow S3 get + CloudWatch logs) ---
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":["sts:AssumeRole","sts:TagSession"]}]}'
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" >/dev/null
fi
PERMS=$(cat <<PERMSJSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:GetObject"],"Resource":"arn:aws:s3:::${BUCKET}/*"},
 {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"arn:aws:logs:*:*:*"}
]}
PERMSJSON
)
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name build-perms --policy-document "$PERMS"
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE_NAME}"
echo "waiting for IAM role to propagate..."; sleep 10

# --- 3. package + upload (Dockerfile + any *.py from $SRC_DIR; Dockerfile must sit at the archive root) ---
( cd "$SRC_DIR" && rm -f "$DIR/app.zip" && zip -j "$DIR/app.zip" Dockerfile *.py >/dev/null )
aws s3 cp "$DIR/app.zip" "s3://${BUCKET}/app.zip"

# --- 4. build the MicroVM image (Lambda runs the Dockerfile + snapshots) ---
# Identifiers must be the image ARN, not the name. Reuse an existing image if present.
IMAGE_ARN=$(aws lambda-microvms list-microvm-images --region "$REGION" \
  --query "items[?name=='${IMAGE_NAME}'].imageArn | [0]" --output text)
if [ -z "$IMAGE_ARN" ] || [ "$IMAGE_ARN" = "None" ]; then
  IMAGE_ARN=$(aws lambda-microvms create-microvm-image --region "$REGION" \
    --name "$IMAGE_NAME" \
    --code-artifact uri="s3://${BUCKET}/app.zip" \
    --base-image-arn "arn:aws:lambda:${REGION}:aws:microvm-image:al2023-1" \
    --build-role-arn "$ROLE_ARN" --query imageArn --output text)
fi
echo "imageArn=$IMAGE_ARN"

echo "building image..."
while true; do
  ST=$(aws lambda-microvms get-microvm-image --region "$REGION" --image-identifier "$IMAGE_ARN" --query state --output text)
  echo "  image: $ST"
  [ "$ST" = "CREATED" ] && break
  if [ "$ST" = "CREATE_FAILED" ]; then
    echo "build failed; see CloudWatch /aws/lambda/microvms/${IMAGE_NAME}" >&2; exit 1
  fi
  sleep 10
done

# --- 5. run a MicroVM ---
RUN=$(aws lambda-microvms run-microvm --region "$REGION" \
  --image-identifier "$IMAGE_ARN" \
  --ingress-network-connectors "arn:aws:lambda:${REGION}:aws:network-connector:aws-network-connector:ALL_INGRESS" \
  --egress-network-connectors  "arn:aws:lambda:${REGION}:aws:network-connector:aws-network-connector:INTERNET_EGRESS")
MVM=$(printf '%s' "$RUN" | python3 -c 'import sys,json;print(json.load(sys.stdin)["microvmId"])')
EP=$(printf '%s' "$RUN"  | python3 -c 'import sys,json;print(json.load(sys.stdin)["endpoint"])')
echo "microvmId=$MVM endpoint=$EP"

cat > "$STATE_FILE" <<STATEEOF
REGION=$REGION
BUCKET=$BUCKET
ROLE_NAME=$ROLE_NAME
IMAGE_NAME=$IMAGE_NAME
IMAGE_ARN=$IMAGE_ARN
MVM=$MVM
STATEEOF

echo "waiting for RUNNING..."
while true; do
  ST=$(aws lambda-microvms get-microvm --region "$REGION" --microvm-identifier "$MVM" --query state --output text)
  echo "  microvm: $ST"
  [ "$ST" = "RUNNING" ] && break
  sleep 5
done

# --- 6. auth token + curl the probe ---
TOKEN=$(aws lambda-microvms create-microvm-auth-token --region "$REGION" \
  --microvm-identifier "$MVM" --expiration-in-minutes 30 \
  --allowed-ports '[{"allPorts":{}}]' --query authToken --output text)

echo "=== PROBE RESULT ==="
# The DinD demo starts dockerd + pulls an image on first request, so allow a generous wait.
for i in 1 2 3 4 5 6; do
  if curl -fsS --max-time 300 "https://${EP}/" -H "X-aws-proxy-auth: ${TOKEN}"; then echo; break; fi
  echo "  (endpoint not ready, retry $i)"; sleep 5
done
echo "Done. Run ./teardown.sh to remove all resources."
