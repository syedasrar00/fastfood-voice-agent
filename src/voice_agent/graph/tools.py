"""Tools exposed to the conversation graph."""
import uuid
from typing import Annotated, Literal
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from voice_agent.adapters.menu_loader import MenuCatalog
from voice_agent.adapters.order_sink import FinalizedOrder, OrderLine, OrderSink, TextFileOrderSink
from voice_agent.config import get_settings

settings = get_settings()
catalog = MenuCatalog(settings.menu_file_path)
order_sink: OrderSink = TextFileOrderSink(settings.orders_file_path)


@tool
def search_menu(query: str) -> str:
    """Search the restaurant menu for items, prices, variants, modifiers, or allergens."""
    results = catalog.search(query)
    if not results:
        return "No matching items found on the menu."
    lines = []
    for item in results:
        variants_str = ", ".join(f"{k}: {v:.0f}" for k, v in item.prices.items())
        mods_str = ", ".join(item.allowed_modifiers) if item.allowed_modifiers else "none"
        allergens_str = ", ".join(item.allergens) if item.allergens else "none"
        lines.append(
            f"SKU={item.sku} | {item.name} ({item.description}) | "
            f"Variants=[{variants_str}] (default={item.default_variant}) | "
            f"Modifiers=[{mods_str}] | Allergens=[{allergens_str}]"
        )
    return "\n".join(lines)


@tool
def add_to_cart(
    sku: str,
    quantity: int,
    variant: str | None,
    modifiers: list[str],
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Add an item to the caller's order cart after checking its SKU on the menu."""
    item = catalog.items.get(sku.upper())
    if not item:
        return Command(
            update={
                "messages": [ToolMessage(content=f"SKU '{sku}' not found. Call search_menu first.", tool_call_id=tool_call_id)]
            }
        )

    chosen_variant = (variant or item.default_variant).lower()
    if chosen_variant not in item.prices:
        chosen_variant = item.default_variant

    allowed_map = {m.lower(): m for m in item.allowed_modifiers}
    valid_mods = sorted({allowed_map[m.lower()] for m in modifiers if m.lower() in allowed_map})
    unit_price = item.prices[chosen_variant]
    qty = max(1, quantity)

    cart: list[dict] = [dict(c) for c in state.get("cart", [])]
    merged = False
    for line in cart:
        if line["sku"] == item.sku and line["variant"] == chosen_variant and line["modifiers"] == valid_mods:
            line["quantity"] += qty
            line["line_total"] = round(line["quantity"] * unit_price, 2)
            merged = True
            break

    if not merged:
        new_line = OrderLine(
            sku=item.sku,
            name=item.name,
            variant=chosen_variant,
            quantity=qty,
            modifiers=valid_mods,
            unit_price=unit_price,
            line_total=round(qty * unit_price, 2),
        )
        cart.append(new_line.model_dump())

    subtotal = sum(c["line_total"] for c in cart)
    return Command(
        update={
            "cart": cart,
            "fallback_count": 0,
            "messages": [
                ToolMessage(
                    content=f"Added {qty}x {item.name} ({chosen_variant}). Cart subtotal is now {subtotal:.0f} {catalog.currency}.",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


@tool
def remove_from_cart(
    sku: str,
    quantity: int,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Remove or reduce the quantity of an item currently in the cart."""
    target_sku = sku.upper()
    updated_cart: list[dict] = []
    removed = False

    for line in state.get("cart", []):
        if line["sku"] == target_sku and not removed:
            removed = True
            remaining = line["quantity"] - max(1, quantity)
            if remaining > 0:
                new_line = dict(line)
                new_line["quantity"] = remaining
                new_line["line_total"] = round(remaining * line["unit_price"], 2)
                updated_cart.append(new_line)
        else:
            updated_cart.append(line)

    if not removed:
        msg = f"Item {target_sku} was not in the cart."
    else:
        subtotal = sum(c["line_total"] for c in updated_cart)
        msg = f"Updated cart. New subtotal is {subtotal:.0f} {catalog.currency}."

    return Command(
        update={
            "cart": updated_cart,
            "messages": [ToolMessage(content=msg, tool_call_id=tool_call_id)],
        }
    )


@tool
def get_cart_summary(state: Annotated[dict, InjectedState]) -> str:
    """Get all items currently in the caller's cart and the total price."""
    cart = state.get("cart", [])
    if not cart:
        return "The cart is currently empty."
    lines = [
        f"{c['quantity']}x {c['name']} ({c['variant']}) "
        f"{'with ' + ', '.join(c['modifiers']) if c['modifiers'] else ''} - {c['line_total']:.0f} {catalog.currency}"
        for c in cart
    ]
    total = sum(c["line_total"] for c in cart)
    return f"Cart items: {'; '.join(lines)}. Total: {total:.0f} {catalog.currency}."


@tool
async def confirm_and_place_order(
    order_type: Literal["pickup", "delivery"],
    notes: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Submit the finalized order once the customer explicitly confirms items, total, and pickup/delivery."""
    cart = state.get("cart", [])
    if not cart:
        return Command(
            update={"messages": [ToolMessage(content="Cart is empty; cannot place order.", tool_call_id=tool_call_id)]}
        )

    lines = [OrderLine(**c) for c in cart]
    order = FinalizedOrder(
        order_id=f"ORD-{uuid.uuid4().hex[:6].upper()}",
        call_sid=state["call_sid"],
        caller_phone=state["caller_phone"],
        order_type=order_type,
        lines=lines,
        subtotal=round(sum(l.line_total for l in lines), 2),
        notes=notes,
    )
    ref_id = await order_sink.submit_order(order)
    return Command(
        update={
            "order_confirmed": True,
            "messages": [
                ToolMessage(
                    content=f"Order placed successfully! Reference ID: {ref_id}. Total: {order.subtotal:.0f} {catalog.currency}.",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


@tool
async def escalate_to_human(
    reason: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Transfer the call to a human team member when unable to resolve or requested by caller."""
    await order_sink.log_transfer(
        call_sid=state["call_sid"],
        caller_phone=state["caller_phone"],
        reason=reason,
        cart=state.get("cart", []),
    )
    return Command(
        update={
            "transfer_requested": True,
            "escalation_reason": reason,
            "messages": [
                ToolMessage(
                    content="Transfer logged. Tell the caller politely that you are connecting them to a team member right now.",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


ALL_TOOLS = [
    search_menu,
    add_to_cart,
    remove_from_cart,
    get_cart_summary,
    confirm_and_place_order,
    escalate_to_human,
]