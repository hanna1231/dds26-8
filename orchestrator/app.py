import os

import redis.asyncio as redis
from redis.asyncio.cluster import RedisCluster, ClusterNode
from quart import Quart, jsonify
from grpc_server import serve_grpc, stop_grpc_server
from transport import COMM_MODE
from recovery import recover_incomplete_sagas, recover_incomplete_tpc
from consumers import setup_consumer_groups, compensation_consumer, audit_consumer, init_stop_event
from events import get_dropped_events, STREAM_NAME, DEAD_LETTERS_STREAM

app = Quart("orchestrator-service")
db = None
_stop_event = None


@app.before_serving
async def startup():
    global db
    node_host = os.environ['REDIS_NODE_HOST']
    node_port = int(os.environ.get('REDIS_NODE_PORT', '6379'))
    db = RedisCluster(
        startup_nodes=[ClusterNode(node_host, node_port)],
        password=os.environ['REDIS_PASSWORD'],
        decode_responses=False,
        require_full_coverage=True,
    )
    await db.initialize()
    if COMM_MODE == "queue":
        from queue_client import init_queue_client
        from reply_listener import setup_reply_consumer_group, reply_listener
        init_queue_client(db)
        await setup_reply_consumer_group(db)
    else:
        from client import init_grpc_clients
        await init_grpc_clients()
    await recover_incomplete_sagas(db)
    await recover_incomplete_tpc(db)
    await setup_consumer_groups(db)
    _stop_event = init_stop_event()
    if COMM_MODE == "queue":
        app.add_background_task(reply_listener, db, _stop_event)
    app.add_background_task(serve_grpc, db)
    app.add_background_task(compensation_consumer, db)
    app.add_background_task(audit_consumer, db)


@app.after_serving
async def shutdown():
    if _stop_event:
        _stop_event.set()
    await stop_grpc_server()
    if COMM_MODE == "queue":
        from queue_client import close_queue_client
        close_queue_client()
    else:
        from client import close_grpc_clients
        await close_grpc_clients()
    await db.aclose()


@app.get('/health')
async def health():
    try:
        await db.ping()
    except Exception:
        return jsonify({"status": "unhealthy"}), 503

    lag_info = {}
    try:
        groups = await db.xinfo_groups(STREAM_NAME)
        lag_info = {g["name"]: g.get("lag", "N/A") for g in groups}
    except Exception:
        pass

    dead_letter_count = 0
    try:
        dead_letter_count = await db.xlen(DEAD_LETTERS_STREAM)
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "consumer_lag": lag_info,
        "dead_letters": dead_letter_count,
        "dropped_events": get_dropped_events(),
    })


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
