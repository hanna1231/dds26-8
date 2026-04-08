import asyncio
import logging
import os
import uuid

import redis.asyncio as redis
import redis.exceptions
from redis.asyncio.cluster import RedisCluster, ClusterNode

from msgspec import msgpack
from quart import Quart, jsonify, abort, Response
from operations import UserValue

DB_ERROR_STR = "DB error"


app = Quart("payment-service")

db = None
queue_db = None
_stop_event = None


@app.before_serving
async def startup():
    global db, queue_db, _stop_event
    node_host = os.environ['REDIS_NODE_HOST']
    node_port = int(os.environ.get('REDIS_NODE_PORT', '6379'))
    db = RedisCluster(
        startup_nodes=[ClusterNode(node_host, node_port)],
        password=os.environ['REDIS_PASSWORD'],
        decode_responses=False,
        require_full_coverage=True,
    )
    await db.initialize()

    # Separate queue cluster for cross-service Redis Stream messaging.
    # Falls back to the domain cluster when QUEUE_REDIS_HOST is unset.
    queue_host = os.environ.get('QUEUE_REDIS_HOST', node_host)
    queue_port = int(os.environ.get('QUEUE_REDIS_PORT', str(node_port)))
    if queue_host == node_host and queue_port == node_port:
        queue_db = db
    else:
        queue_db = RedisCluster(
            startup_nodes=[ClusterNode(queue_host, queue_port)],
            password=os.environ['REDIS_PASSWORD'],
            decode_responses=False,
            require_full_coverage=True,
        )
        await queue_db.initialize()

    from queue_consumer import setup_command_consumer_group, queue_consumer
    _stop_event = asyncio.Event()
    await setup_command_consumer_group(queue_db)
    app.add_background_task(queue_consumer, db, queue_db, _stop_event)


@app.after_serving
async def shutdown():
    if _stop_event:
        _stop_event.set()
    if queue_db is not db:
        await queue_db.aclose()
    await db.aclose()


async def get_user_from_db(user_id: str) -> UserValue | None:
    try:
        # get serialized data
        entry: bytes = await db.get(f"{{user:{user_id}}}")
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    # deserialize data if it exists else return null
    entry: UserValue | None = msgpack.decode(entry, type=UserValue) if entry else None
    if entry is None:
        # if user does not exist in the database; abort
        abort(400, f"User: {user_id} not found!")
    return entry


@app.get('/health')
async def health():
    try:
        await db.ping()
        return jsonify({"status": "ok"}), 200
    except Exception:
        return jsonify({"status": "unhealthy"}), 503


@app.post('/create_user')
async def create_user():
    key = str(uuid.uuid4())
    value = msgpack.encode(UserValue(credit=0))
    try:
        await db.set(f"{{user:{key}}}", value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({'user_id': key})


@app.post('/batch_init/<n>/<starting_money>')
async def batch_init_users(n: int, starting_money: int):
    n = int(n)
    starting_money = int(starting_money)
    try:
        pipe = db.pipeline(transaction=False)
        for i in range(n):
            pipe.set(f"{{user:{i}}}", msgpack.encode(UserValue(credit=starting_money)))
        await pipe.execute()
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"msg": "Batch init for users successful"})


@app.get('/find_user/<user_id>')
async def find_user(user_id: str):
    user_entry: UserValue = await get_user_from_db(user_id)
    return jsonify(
        {
            "user_id": user_id,
            "credit": user_entry.credit
        }
    )


@app.post('/add_funds/<user_id>/<amount>')
async def add_credit(user_id: str, amount: int):
    user_entry: UserValue = await get_user_from_db(user_id)
    # update credit, serialize and update database
    user_entry.credit += int(amount)
    try:
        await db.set(f"{{user:{user_id}}}", msgpack.encode(user_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"User: {user_id} credit updated to: {user_entry.credit}", status=200)


@app.post('/pay/<user_id>/<amount>')
async def remove_credit(user_id: str, amount: int):
    app.logger.debug(f"Removing {amount} credit from user: {user_id}")
    user_entry: UserValue = await get_user_from_db(user_id)
    # update credit, serialize and update database
    user_entry.credit -= int(amount)
    if user_entry.credit < 0:
        abort(400, f"User: {user_id} credit cannot get reduced below zero!")
    try:
        await db.set(f"{{user:{user_id}}}", msgpack.encode(user_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"User: {user_id} credit updated to: {user_entry.credit}", status=200)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
