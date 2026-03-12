import logging
import os
import uuid

import redis.asyncio as redis
import redis.exceptions
from redis.asyncio.cluster import RedisCluster, ClusterNode

from msgspec import msgpack
from quart import Quart, jsonify, abort, Response
from grpc_server import serve_grpc, stop_grpc_server
from operations import StockValue


DB_ERROR_STR = "DB error"

app = Quart("stock-service")

db = None


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
    app.add_background_task(serve_grpc, db)


@app.after_serving
async def shutdown():
    await stop_grpc_server()
    await db.aclose()


async def get_item_from_db(item_id: str) -> StockValue | None:
    # get serialized data
    try:
        entry: bytes = await db.get(f"{{item:{item_id}}}")
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    # deserialize data if it exists else return null
    entry: StockValue | None = msgpack.decode(entry, type=StockValue) if entry else None
    if entry is None:
        # if item does not exist in the database; abort
        abort(400, f"Item: {item_id} not found!")
    return entry


@app.get('/health')
async def health():
    try:
        await db.ping()
        return jsonify({"status": "ok"}), 200
    except Exception:
        return jsonify({"status": "unhealthy"}), 503


@app.post('/item/create/<price>')
async def create_item(price: int):
    key = str(uuid.uuid4())
    app.logger.debug(f"Item: {key} created")
    value = msgpack.encode(StockValue(stock=0, price=int(price)))
    try:
        await db.set(f"{{item:{key}}}", value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({'item_id': key})


@app.post('/batch_init/<n>/<starting_stock>/<item_price>')
async def batch_init_users(n: int, starting_stock: int, item_price: int):
    n = int(n)
    starting_stock = int(starting_stock)
    item_price = int(item_price)
    try:
        pipe = db.pipeline(transaction=False)
        for i in range(n):
            pipe.set(f"{{item:{i}}}", msgpack.encode(StockValue(stock=starting_stock, price=item_price)))
        await pipe.execute()
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"msg": "Batch init for stock successful"})


@app.get('/find/<item_id>')
async def find_item(item_id: str):
    item_entry: StockValue = await get_item_from_db(item_id)
    return jsonify(
        {
            "stock": item_entry.stock,
            "price": item_entry.price
        }
    )


@app.post('/add/<item_id>/<amount>')
async def add_stock(item_id: str, amount: int):
    item_entry: StockValue = await get_item_from_db(item_id)
    # update stock, serialize and update database
    item_entry.stock += int(amount)
    try:
        await db.set(f"{{item:{item_id}}}", msgpack.encode(item_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)


@app.post('/subtract/<item_id>/<amount>')
async def remove_stock(item_id: str, amount: int):
    item_entry: StockValue = await get_item_from_db(item_id)
    # update stock, serialize and update database
    item_entry.stock -= int(amount)
    app.logger.debug(f"Item: {item_id} stock updated to: {item_entry.stock}")
    if item_entry.stock < 0:
        abort(400, f"Item: {item_id} stock cannot get reduced below zero!")
    try:
        await db.set(f"{{item:{item_id}}}", msgpack.encode(item_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
