"""Conversation workflow definition."""

import re
from collections.abc import AsyncIterator
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from voice_agent.config import get_settings
from voice_agent.graph.prompts import build_system_prompt
from voice_agent.graph.state import OrderCallState
from voice_agent.graph.tools import ALL_TOOLS, catalog

settings = get_settings()
SYSTEM_PROMPT = build_system_prompt(catalog.restaurant_name, catalog.currency, catalog.store_hours)

llm = ChatOpenAI(
    model=settings.llm_model,
    api_key=settings.openai_api_key,
    temperature=0.2,
    streaming=True,
).bind_tools(ALL_TOOLS)


async def agent_node(state: OrderCallState) -> dict:
    messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


builder = StateGraph(OrderCallState)
builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode(ALL_TOOLS))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools", "agent")

memory = MemorySaver()
voice_graph = builder.compile(checkpointer=memory)

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


async def stream_spoken_sentences(
    user_text: str,
    call_sid: str,
    caller_phone: str = "unknown",
) -> AsyncIterator[str]:
    """Stream complete sentences from LangGraph as soon as punctuation arrives for low-latency TTS."""
    config = {"configurable": {"thread_id": call_sid}}
    existing = await voice_graph.aget_state(config)

    if not existing.values:
        graph_input = {
            "messages": [HumanMessage(content=user_text)],
            "call_sid": call_sid,
            "caller_phone": caller_phone,
            "cart": [],
            "order_confirmed": False,
            "fallback_count": 0,
            "transfer_requested": False,
            "escalation_reason": None,
        }
    else:
        graph_input = {"messages": [HumanMessage(content=user_text)]}

    buffer = ""
    async for msg_chunk, metadata in voice_graph.astream(
        graph_input, config=config, stream_mode="messages"
    ):
        if metadata.get("langgraph_node") == "agent" and isinstance(msg_chunk, AIMessageChunk):
            if msg_chunk.content:
                buffer += msg_chunk.content
                parts = SENTENCE_SPLIT_RE.split(buffer)
                for sentence in parts[:-1]:
                    clean = sentence.strip()
                    if clean:
                        yield clean
                buffer = parts[-1]

    if buffer.strip():
        yield buffer.strip()