from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class ChargePaymentRequest(_message.Message):
    __slots__ = ("user_id", "amount", "idempotency_key")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    amount: int
    idempotency_key: str
    def __init__(self, user_id: _Optional[str] = ..., amount: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class RefundPaymentRequest(_message.Message):
    __slots__ = ("user_id", "amount", "idempotency_key")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    amount: int
    idempotency_key: str
    def __init__(self, user_id: _Optional[str] = ..., amount: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CheckPaymentRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    def __init__(self, user_id: _Optional[str] = ...) -> None: ...

class PaymentResponse(_message.Message):
    __slots__ = ("success", "error_message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error_message: str
    def __init__(self, success: bool = ..., error_message: _Optional[str] = ...) -> None: ...

class CheckPaymentResponse(_message.Message):
    __slots__ = ("success", "error_message", "credit")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CREDIT_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error_message: str
    credit: int
    def __init__(self, success: bool = ..., error_message: _Optional[str] = ..., credit: _Optional[int] = ...) -> None: ...
