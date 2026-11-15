"""Local CLI for testing ordering and escalation without Twilio."""

import asyncio
import uuid
from voice_agent.graph.tools import catalog
from voice_agent.graph.workflow import stream_spoken_sentences, voice_graph


async def main() -> None:
    call_sid = f"POC-{uuid.uuid4().hex[:6].upper()}"
    print(f"--- Simulated Call ({call_sid}) | {catalog.restaurant_name} ---")
    print("Agent: Welcome! What can I get started for you today?\n")

    while True:
        user_input = input("Caller: ").strip()
        if not user_input or user_input.lower() in {"exit", "quit"}:
            break

        print("Agent: ", end="", flush=True)
        async for sentence in stream_spoken_sentences(user_input, call_sid=call_sid, caller_phone="+919999999999"):
            print(sentence, end=" ", flush=True)
        print("\n")

        state = await voice_graph.aget_state({"configurable": {"thread_id": call_sid}})
        if state.values.get("transfer_requested"):
            print(f"[SYSTEM: Call transferred to human | Reason: {state.values.get('escalation_reason')}]")
            print("[SYSTEM: Handoff snapshot written to data/orders.txt]")
            break
        if state.values.get("order_confirmed"):
            print("[SYSTEM: Order finalized and written to data/orders.txt]")


if __name__ == "__main__":
    asyncio.run(main())