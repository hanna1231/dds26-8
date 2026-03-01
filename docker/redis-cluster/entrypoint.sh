#!/bin/bash
# Redis Cluster node entrypoint — mimics bitnami/redis-cluster behavior.
#
# Environment variables:
#   REDIS_PASSWORD         - requirepass / masterauth password (default: none)
#   REDIS_PORT_NUMBER      - port to listen on (default: 6379)
#   REDIS_NODES            - space-separated list of node hostnames (for cluster creator)
#   REDIS_CLUSTER_CREATOR  - if "yes", this node will run redis-cli --cluster create
#   REDIS_CLUSTER_REPLICAS - number of replicas per master (default: 1)
#   REDISCLI_AUTH          - password for redis-cli (used by creator node)

set -e

PORT="${REDIS_PORT_NUMBER:-6379}"
PASSWORD="${REDIS_PASSWORD:-}"
CLUSTER_CREATOR="${REDIS_CLUSTER_CREATOR:-no}"
REPLICAS="${REDIS_CLUSTER_REPLICAS:-1}"

# Build redis.conf dynamically
CONF_FILE="/tmp/redis-cluster.conf"
cat > "$CONF_FILE" <<EOF
port ${PORT}
cluster-enabled yes
cluster-config-file /data/nodes.conf
cluster-node-timeout 5000
appendonly yes
appendfsync everysec
loglevel notice
save ""
EOF

if [ -n "$PASSWORD" ]; then
    echo "requirepass ${PASSWORD}" >> "$CONF_FILE"
    echo "masterauth ${PASSWORD}" >> "$CONF_FILE"
fi

# Start Redis server in the background if this is the cluster creator
if [ "$CLUSTER_CREATOR" = "yes" ]; then
    redis-server "$CONF_FILE" &
    SERVER_PID=$!

    echo "Waiting for all cluster nodes to become available..."
    # Parse REDIS_NODES env var (space-separated hostnames)
    NODES=($REDIS_NODES)
    NODE_ADDRS=""
    for NODE in "${NODES[@]}"; do
        NODE_ADDRS="$NODE_ADDRS ${NODE}:${PORT}"
    done
    NODE_ADDRS="${NODE_ADDRS# }"  # trim leading space

    # Wait for all nodes to be reachable
    for NODE in "${NODES[@]}"; do
        echo "Waiting for ${NODE}:${PORT}..."
        until redis-cli -h "$NODE" -p "$PORT" ${PASSWORD:+-a "$PASSWORD"} --no-auth-warning ping 2>/dev/null | grep -q PONG; do
            sleep 1
        done
        echo "${NODE}:${PORT} is ready"
    done

    # Check if cluster is already initialized
    CLUSTER_INFO=$(redis-cli -h localhost -p "$PORT" ${PASSWORD:+-a "$PASSWORD"} --no-auth-warning cluster info 2>/dev/null || true)
    if echo "$CLUSTER_INFO" | grep -q "cluster_state:ok"; then
        echo "Cluster already initialized, skipping cluster create."
    else
        echo "Creating Redis cluster with nodes: $NODE_ADDRS"
        yes yes | redis-cli --cluster create $NODE_ADDRS \
            --cluster-replicas "$REPLICAS" \
            ${PASSWORD:+--pass "$PASSWORD"} \
            --no-auth-warning
        echo "Redis cluster created successfully."
    fi

    # Keep foreground by waiting for the server
    wait "$SERVER_PID"
else
    # Regular node: start in foreground
    exec redis-server "$CONF_FILE"
fi
