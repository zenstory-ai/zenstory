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

# 注册所有 Deployments
echo "Deploying flows from prefect.yaml..."
prefect deploy --all

# 启动 Prefect Worker
echo "Starting worker on pool: $POOL_NAME"
exec prefect worker start --pool "$POOL_NAME"
