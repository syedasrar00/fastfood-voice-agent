"""Order persistence adapter."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
import aiofiles
import httpx
from pydantic import BaseModel, Field


class OrderLine(BaseModel):
    sku: str
    name: str
    variant: str
    quantity: int
    modifiers: list[str] = Field(default_factory=list)
    unit_price: float
    line_total: float


class FinalizedOrder(BaseModel):
    order_id: str
    call_sid: str
    caller_phone: str
    order_type: str
    lines: list[OrderLine]
    subtotal: float
    notes: str = ""


class OrderSink(Protocol):
    async def submit_order(self, order: FinalizedOrder) -> str: ...
    async def log_transfer(self, call_sid: str, caller_phone: str, reason: str, cart: list[dict]) -> None: ...


class TextFileOrderSink:
    """POC file sink writing kitchen tickets and human-transfer handoffs to a text file."""

    def __init__(self, file_path: str | Path = "data/orders.txt"):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def submit_order(self, order: FinalizedOrder) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        items_block = "\n".join(
            f"  - {l.quantity}x {l.name} ({l.variant}) "
            f"[{', '.join(l.modifiers) if l.modifiers else 'standard'}] = {l.line_total:.2f}"
            for l in order.lines
        )
        ticket = (
            f"========================================\n"
            f"TYPE       : NEW ORDER ({order.order_type.upper()})\n"
            f"ORDER ID   : {order.order_id}\n"
            f"TIME       : {ts}\n"
            f"CALL SID   : {order.call_sid}\n"
            f"PHONE      : {order.caller_phone}\n"
            f"ITEMS:\n{items_block}\n"
            f"SUBTOTAL   : {order.subtotal:.2f}\n"
            f"NOTES      : {order.notes or 'None'}\n"
            f"========================================\n\n"
        )
        async with self._lock:
            async with aiofiles.open(self.file_path, mode="a", encoding="utf-8") as f:
                await f.write(ticket)
        return order.order_id

    async def log_transfer(self, call_sid: str, caller_phone: str, reason: str, cart: list[dict]) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        cart_summary = (
            "\n".join(f"  - {c['quantity']}x {c['name']} ({c['variant']})" for c in cart)
            if cart
            else "  (Empty cart)"
        )
        ticket = (
            f"----------------------------------------\n"
            f"TYPE       : HUMAN TRANSFER HANDOFF\n"
            f"TIME       : {ts}\n"
            f"CALL SID   : {call_sid}\n"
            f"PHONE      : {caller_phone}\n"
            f"REASON     : {reason}\n"
            f"CART SNAPSHOT:\n{cart_summary}\n"
            f"----------------------------------------\n\n"
        )
        async with self._lock:
            async with aiofiles.open(self.file_path, mode="a", encoding="utf-8") as f:
                await f.write(ticket)


class HttpApiOrderSink:
    """Drop-in replacement when the restaurant provides their live POS endpoint."""

    def __init__(self, endpoint_url: str, api_key: str):
        self.endpoint_url = endpoint_url
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def submit_order(self, order: FinalizedOrder) -> str:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{self.endpoint_url}/orders", json=order.model_dump(), headers=self.headers)
            resp.raise_for_status()
            return resp.json().get("order_id", order.order_id)

    async def log_transfer(self, call_sid: str, caller_phone: str, reason: str, cart: list[dict]) -> None:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{self.endpoint_url}/handoffs",
                json={"call_sid": call_sid, "caller_phone": caller_phone, "reason": reason, "cart": cart},
                headers=self.headers,
            )