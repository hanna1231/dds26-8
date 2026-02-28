import os
import random
import uuid
from collections import defaultdict

import redis.asyncio as redis
import httpx
import grpc.aio

from msgspec import msgpack, Struct
from quart import Quart, jsonify, abort, Response

from orchestrator_pb2 import CheckoutRequest, LineItem
from orchestrator_pb2_grpc import OrchestratorServiceStub


DB_ERROR_STR = "DB error"
REQ_ERROR_STR = "Requests error"

GATEWAY_URL = os.environ['GATEWAY_URL']
ORCHESTRATOR_ADDR = os.environ.get("ORCHESTRATOR_GRPC_ADDR", "orchestrator-service:50053")

app = Quart("order-service")

db: redis.Redis = None
http_client: httpx.AsyncClient = None
_orchestrator_channel = None
_orchestrator_stub: OrchestratorServiceStub = None


@app.before_serving
async def startup():
    global db, http_client, _orchestrator_channel, _orchestrator_stub
    db = redis.Redis(host=os.environ['REDIS_HOST'],
                     port=int(os.environ['REDIS_PORT']),
                     password=os.environ['REDIS_PASSWORD'],
                     db=int(os.environ['REDIS_DB']))
    http_client = httpx.AsyncClient()
    _orchestrator_channel = grpc.aio.insecure_channel(ORCHESTRATOR_ADDR)
    _orchestrator_stub = OrchestratorServiceStub(_orchestrator_channel)


@app.after_serving
async def shutdown():
    if _orchestrator_channel:
        await _orchestrator_channel.close()
    await db.aclose()
    await http_client.aclose()


class OrderValue(Struct):
    paid: bool
    items: list[tuple[str, int]]
    user_id: str
    total_cost: int


async def get_order_from_db(order_id: str) -> OrderValue | None:
    try:
        # get serialized data
        entry: bytes = await db.get(order_id)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    # deserialize data if it exists else return null
    entry: OrderValue | None = msgpack.decode(entry, type=OrderValue) if entry else None
    if entry is None:
        # if order does not exist in the database; abort
        abort(400, f"Order: {order_id} not found!")
    return entry


@app.post('/create/<user_id>')
async def create_order(user_id: str):
    key = str(uuid.uuid4())
    value = msgpack.encode(OrderValue(paid=False, items=[], user_id=user_id, total_cost=0))
    try:
        await db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({'order_id': key})


@app.post('/batch_init/<n>/<n_items>/<n_users>/<item_price>')
async def batch_init_users(n: int, n_items: int, n_users: int, item_price: int):

    n = int(n)
    n_items = int(n_items)
    n_users = int(n_users)
    item_price = int(item_price)

    def generate_entry() -> OrderValue:
        user_id = random.randint(0, n_users - 1)
        item1_id = random.randint(0, n_items - 1)
        item2_id = random.randint(0, n_items - 1)
        value = OrderValue(paid=False,
                           items=[(f"{item1_id}", 1), (f"{item2_id}", 1)],
                           user_id=f"{user_id}",
                           total_cost=2*item_price)
        return value

    kv_pairs: dict[str, bytes] = {f"{i}": msgpack.encode(generate_entry())
                                  for i in range(n)}
    try:
        await db.mset(kv_pairs)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"msg": "Batch init for orders successful"})


@app.get('/find/<order_id>')
async def find_order(order_id: str):
    order_entry: OrderValue = await get_order_from_db(order_id)
    return jsonify(
        {
            "order_id": order_id,
            "paid": order_entry.paid,
            "items": order_entry.items,
            "user_id": order_entry.user_id,
            "total_cost": order_entry.total_cost
        }
    )


async def send_get_request(url: str):
    try:
        response = await http_client.get(url)
    except httpx.RequestError:
        abort(400, REQ_ERROR_STR)
    else:
        return response


@app.post('/addItem/<order_id>/<item_id>/<quantity>')
async def add_item(order_id: str, item_id: str, quantity: int):
    order_entry: OrderValue = await get_order_from_db(order_id)
    item_reply = await send_get_request(f"{GATEWAY_URL}/stock/find/{item_id}")
    if item_reply.status_code != 200:
        # Request failed because item does not exist
        abort(400, f"Item: {item_id} does not exist!")
    item_json: dict = item_reply.json()
    order_entry.items.append((item_id, int(quantity)))
    order_entry.total_cost += int(quantity) * item_json["price"]
    try:
        await db.set(order_id, msgpack.encode(order_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"Item: {item_id} added to: {order_id} price updated to: {order_entry.total_cost}",
                    status=200)


@app.post('/checkout/<order_id>')
async def checkout(order_id: str):
    app.logger.debug(f"Checking out {order_id}")
    order_entry: OrderValue = await get_order_from_db(order_id)

    # Aggregate items: combine duplicate item_ids
    items_quantities: dict[str, int] = defaultdict(int)
    for item_id, quantity in order_entry.items:
        items_quantities[item_id] += quantity

    # Build LineItem list for proto
    line_items = [LineItem(item_id=item_id, quantity=quantity)
                  for item_id, quantity in items_quantities.items()]

    # Single gRPC call to orchestrator — SAGA handles everything
    resp = await _orchestrator_stub.StartCheckout(
        CheckoutRequest(
            order_id=order_id,
            user_id=order_entry.user_id,
            items=line_items,
            total_cost=order_entry.total_cost,
        ),
        timeout=60.0,  # SAGA is synchronous — longer timeout needed
    )

    if not resp.success:
        abort(400, resp.error_message)

    # Mark order as paid in Order's own Redis
    order_entry.paid = True
    try:
        await db.set(order_id, msgpack.encode(order_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)

    app.logger.debug("Checkout successful")
    return Response("Checkout successful", status=200)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
