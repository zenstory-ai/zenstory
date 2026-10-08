#!/bin/bash
# Prefect Worker 启动脚本
# 支持 Railway 环境变量

set -e

POOL_NAME=${PREFECT_WORK_POOL:-zenstory-pool}

# 等待 Prefect Server 就绪
echo "Waiting for Prefect Server..."
server_ready=false
for i in $(seq 1 30); do
  if python3 -c "import urllib.request; urllib.request.urlopen('${PREFECT_API_URL}/health')" 2>/dev/null; then
    echo "Prefect Server is ready."
    server_ready=true
    break
  fi
  echo "Attempt $i/30 - Prefect Server not ready, retrying..."
  sleep 5
done

if [ "$server_ready" != "true" ]; then
  echo "ERROR: Prefect Server did not become ready within 150 seconds."
  exit 1
fi

# 创建 Work Pool（如果不存在）
echo "Creating work pool: $POOL_NAME"
prefect work-pool create "$POOL_NAME" --type process 2>/dev/null || echo "Work pool already exists"

# Prefect 3.6.28 ignores CLI options such as --pool when --all selects multiple
# deployments. Deploy each configured name separately so the environment's pool
# overrides the production default stored in prefect.yaml.
DEPLOYMENT_NAMES=$(python3 - <<'PY'
import re

import yaml

with open("prefect.yaml", encoding="utf-8") as handle:
    deployments = (yaml.safe_load(handle) or {}).get("deployments") or []
names = [deployment.get("name") for deployment in deployments if isinstance(deployment, dict)]
if not names or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name) for name in names):
    raise SystemExit("prefect.yaml must contain filesystem-safe deployment names")
if len(names) != len(set(names)):
    raise SystemExit("prefect.yaml deployment names must be unique")
print("\n".join(names))
PY
)

while IFS= read -r deployment_name; do
  echo "Deploying $deployment_name to pool: $POOL_NAME"
  prefect deploy --name "$deployment_name" --pool "$POOL_NAME"
done <<< "$DEPLOYMENT_NAMES"

# 启动 Prefect Worker
echo "Starting worker on pool: $POOL_NAME"
exec prefect worker start --pool "$POOL_NAME"
