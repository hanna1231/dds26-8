import os

import redis.asyncio as redis
from quart import Quart, jsonify
from grpc_server import serve_grpc, stop_grpc_server
from client import init_grpc_clients, close_grpc_clients
from recovery import recover_incomplete_sagas

app = Quart("orchestrator-service")
db: redis.Redis = None


@app.before_serving
async def startup():
    global db
    db = redis.Redis(
        host=os.environ['REDIS_HOST'],
        port=int(os.environ['REDIS_PORT']),
        password=os.environ['REDIS_PASSWORD'],
        db=int(os.environ['REDIS_DB']),
    )
    await init_grpc_clients()
    await recover_incomplete_sagas(db)
    app.add_background_task(serve_grpc, db)


@app.after_serving
async def shutdown():
    await stop_grpc_server()
    await close_grpc_clients()
    await db.aclose()


@app.get('/health')
async def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
