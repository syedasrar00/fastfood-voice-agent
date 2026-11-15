"""Conversation state definitions."""
from typing import Annotated, Literal, TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class OrderCallState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    call_sid: str
    caller_phone: str
    cart: list[dict]
    order_confirmed: bool
    fallback_count: int
    transfer_requested: bool
    escalation_reason: str | None