from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class ReserveStockRequest(_message.Message):
    __slots__ = ("item_id", "quantity", "idempotency_key")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    quantity: int
    idempotency_key: str
    def __init__(self, item_id: _Optional[str] = ..., quantity: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ReleaseStockRequest(_message.Message):
    __slots__ = ("item_id", "quantity", "idempotency_key")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    quantity: int
    idempotency_key: str
    def __init__(self, item_id: _Optional[str] = ..., quantity: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CheckStockRequest(_message.Message):
    __slots__ = ("item_id",)
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    def __init__(self, item_id: _Optional[str] = ...) -> None: ...

class StockResponse(_message.Message):
    __slots__ = ("success", "error_message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error_message: str
    def __init__(self, success: bool = ..., error_message: _Optional[str] = ...) -> None: ...

class CheckStockResponse(_message.Message):
    __slots__ = ("success", "error_message", "stock", "price")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    STOCK_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error_message: str
    stock: int
    price: int
    def __init__(self, success: bool = ..., error_message: _Optional[str] = ..., stock: _Optional[int] = ..., price: _Optional[int] = ...) -> None: ...
