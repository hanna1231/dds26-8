from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class LineItem(_message.Message):
    __slots__ = ("item_id", "quantity")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    quantity: int
    def __init__(self, item_id: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class CheckoutRequest(_message.Message):
    __slots__ = ("order_id", "user_id", "items", "total_cost")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COST_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    user_id: str
    items: _containers.RepeatedCompositeFieldContainer[LineItem]
    total_cost: int
    def __init__(self, order_id: _Optional[str] = ..., user_id: _Optional[str] = ..., items: _Optional[_Iterable[_Union[LineItem, _Mapping]]] = ..., total_cost: _Optional[int] = ...) -> None: ...

class CheckoutResponse(_message.Message):
    __slots__ = ("success", "error_message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error_message: str
    def __init__(self, success: bool = ..., error_message: _Optional[str] = ...) -> None: ...
