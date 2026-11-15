# Fast-Food Voice Ordering Agent

A low-latency, conversational AI voice agent built with **Python**, **uv**, **LangGraph**, and **FastAPI** for fast-food outlets. It handles incoming phone orders, answers menu and allergen questions, supports real-time caller interruptions (*barge-in*), and performs warm call transfers to human staff when a request cannot be resolved.

Designed with an **adapter-first POC architecture**: menu data loads from a structured `menu.json` file and finalized orders/handoff tickets persist to `data/orders.txt`, allowing end-to-end testing before integrating the restaurant's live POS API.

---

## Key Capabilities

* **Sub-800ms Streaming Voice Pipeline:** Pipes bidirectional `mulaw` (8 kHz) audio between Twilio Media Streams, Deepgram Nova-2 STT, LangGraph, and Cartesia Sonic-2 TTS without audio resampling overhead.
* **Sentence-Boundary TTS Dispatch:** Streams LLM tokens into a punctuation chunker so the first sentence begins speaking before the full response finishes generating.
* **Real-Time Barge-In Handling:** Listens for Deepgram `SpeechStarted` VAD events to cancel active LLM/TTS generation tasks and flush Twilio's outbound audio buffer the moment a caller interrupts.
* **Deterministic Cart & Menu Math:** Uses LangGraph `InjectedState` and `Command` tools (`search_menu`, `add_to_cart`, `remove_from_cart`, `confirm_and_place_order`) backed by Pydantic validation—preventing price hallucinations or arithmetic errors.
* **Smart Human Escalation:** Automatically logs a cart snapshot and transfers the live call via Twilio `<Dial>` when a customer asks for a manager/human, raises a complaint/refund, or triggers two consecutive unresolved turns.
* **Drop-In POS API Readiness:** Isolates order persistence behind the `OrderSink` protocol (`TextFileOrderSink` for POC, `HttpApiOrderSink` for production).

---

## Architecture

```text
[Customer Phone (PSTN / SIP)]
             │
             ▼  (Twilio Media Streams: mulaw 8kHz WebSocket)
┌────────────────────────────────────────────────────────────┐
│  Media Plane (src/voice_agent/main.py & media/stt_tts.py)  │
│  ┌──────────────┐    ┌───────────────┐    ┌─────────────┐  │
│  │ Deepgram VAD │───▶│ Streaming STT │───▶│ Cartesia    │  │
│  │ (Barge-In)   │    │ (Keyword Boost│    │ Sonic TTS   │  │
│  └──────────────┘    └───────┬───────┘    └──────▲──────┘  │
└──────────────────────────────┼───────────────────┼─────────┘
                               │ Final             │ Sentence
                               │ Utterance         │ Chunks
                               ▼                   │
┌────────────────────────────────────────────────────────────┐
│  Cognitive & State Plane (LangGraph + MemorySaver)         │
│                                                            │
│   [Agent Node (gpt-4o-mini)] ◀──▶ [Deterministic Tools]    │
│                                      ├── MenuCatalog       │
│                                      │   (data/menu.json)  │
│                                      ├── OrderSink         │
│                                      │   (data/orders.txt) │
│                                      └── Twilio Transfer   │
└────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
| :--- | :--- |
| **Runtime & Packaging** | Python 3.11+, `uv` |
| **Agent Orchestration** | LangGraph (`StateGraph`, `MemorySaver`, `Command`), LangChain Core, `langchain-openai` |
| **Web & WebSocket Server** | FastAPI, Uvicorn, `websockets` |
| **Speech-to-Text (STT)** | Deepgram Nova-2 (Streaming WebSocket + Menu Keyword Boosting) |
| **Text-to-Speech (TTS)** | Cartesia Sonic-2 (Streaming WebSocket, native `pcm_mulaw` 8kHz) |
| **Telephony** | Twilio Programmable Voice & Media Streams |
| **Validation & Async I/O** | Pydantic v2, `pydantic-settings`, `aiofiles`, `httpx` |

---

## Project Structure

```text
fastfood-voice-agent/
├── pyproject.toml                  # Project dependencies and build config (uv)
├── .env.example                    # Environment variable template
├── README.md
├── data/
│   ├── menu.json                   # Structured restaurant menu, variants, modifiers & allergens
│   └── orders.txt                  # Auto-generated POC kitchen tickets & human transfer logs
└── src/
    └── voice_agent/
        ├── __init__.py
        ├── config.py               # Pydantic settings loader
        ├── cli_chat.py             # Terminal simulator for text-based call testing
        ├── main.py                 # FastAPI app, Twilio webhooks & WebSocket audio loop
        ├── adapters/
        │   ├── __init__.py
        │   ├── menu_loader.py      # Menu search, alias matching & STT keyword extraction
        │   └── order_sink.py       # OrderSink protocol, TextFileOrderSink & HttpApiOrderSink
        ├── graph/
        │   ├── __init__.py
        │   ├── state.py            # OrderCallState TypedDict schema
        │   ├── prompts.py          # Spoken-style system prompt rules
        │   ├── tools.py            # Cart, menu, order confirmation & escalation tools
        │   └── workflow.py         # LangGraph graph definition & sentence streamer
        └── media/
            ├── __init__.py
            └── stt_tts.py          # Deepgram & Cartesia WebSocket streaming clients
```

---

## Quickstart Setup

### 1. Prerequisites

* **Python 3.11+**
* **[uv](https://docs.astral.sh/uv/)** installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
* **OpenAI API Key** (required for both CLI and phone modes)
* **Deepgram, Cartesia, and Twilio accounts** (required only for live PSTN phone calls)

### 2. Install Dependencies

```bash
cd fastfood-voice-agent
uv sync
```

### 3. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

| Variable | Required | Description |
| :--- | :--- | :--- |
| `OPENAI_API_KEY` | **Yes** | OpenAI API key for the LangGraph reasoning model |
| `LLM_MODEL` | No | Defaults to `gpt-4o-mini` for low time-to-first-token latency |
| `DEEPGRAM_API_KEY` | Live Calls | Deepgram API key for streaming speech recognition |
| `CARTESIA_API_KEY` | Live Calls | Cartesia API key for streaming voice synthesis |
| `CARTESIA_VOICE_ID` | Live Calls | Voice ID from Cartesia Voice Library |
| `TWILIO_ACCOUNT_SID` | Live Calls | Twilio Account SID (needed for human call transfers) |
| `TWILIO_AUTH_TOKEN` | Live Calls | Twilio Auth Token |
| `HUMAN_TRANSFER_NUMBER` | Live Calls | E.164 phone number of the store counter/manager (e.g., `+919876543210`) |
| `MENU_FILE_PATH` | No | Path to menu JSON (default: `data/menu.json`) |
| `ORDERS_FILE_PATH` | No | Path to POC order output file (default: `data/orders.txt`) |

---

## Running the POC

### Mode 1: Local CLI Call Simulator (No Telephony Required)

Test order taking, menu/allergen queries, cart updates, and human escalation directly in your terminal:

```bash
uv run python -m voice_agent.cli_chat
```

**Sample Test Scenarios:**
* **Multi-item order:** *"Can I get two double smash burgers with extra cheese and a large peri peri fries for pickup?"*
* **Allergen inquiry:** *"Do your peri peri fries contain gluten?"*
* **Order edit:** *"Actually, remove one burger and add a regular cola with no ice."*
* **Human transfer:** *"I have an issue with my previous order, let me talk to a manager."*

Inspect `data/orders.txt` after confirming an order or triggering an escalation to verify the formatted ticket output.

---

### Mode 2: Live Phone Calls with Twilio

1. **Start the FastAPI server:**
   ```bash
   uv run uvicorn voice_agent.main:app --host 0.0.0.0 --port 8000 --reload
   ```

2. **Expose port `8000` over HTTPS/WSS** using `ngrok` or Cloudflare Tunnel:
   ```bash
   ngrok http 8000
   ```

3. **Configure your Twilio Phone Number:**
   * Open the **Twilio Console** → **Phone Numbers** → **Manage** → **Active Numbers**.
   * Under **Voice Configuration** → **A call comes in**, select **Webhook**, paste `https://<your-ngrok-domain>/voice/incoming`, and set method to **HTTP POST**.

4. **Verify health check:**
   ```bash
   curl http://localhost:8000/health
   ```

---

## Customizing the Menu (`data/menu.json`)

Update `data/menu.json` with the restaurant's items. Each item supports spoken `aliases` (automatically fed into Deepgram's keyword booster to improve speech recognition accuracy for proprietary item names):

```json
{
  "sku": "BRG-01",
  "name": "Classic Smash Burger",
  "aliases": ["smash burger", "classic burger", "beef burger"],
  "category": "burgers",
  "prices": { "single": 180.0, "double": 250.0 },
  "default_variant": "single",
  "allowed_modifiers": ["extra cheese", "no onions", "no pickles"],
  "allergens": ["gluten", "dairy", "sesame"],
  "description": "Seared smash patty with cheddar, pickles, and house sauce.",
  "available": true
}
```

---

## Order Ticket Format & POS API Migration

### Current POC Output (`data/orders.txt`)

Confirmed orders and escalation handoffs are appended thread-safely to `data/orders.txt`:

```text
========================================
TYPE       : NEW ORDER (PICKUP)
ORDER ID   : ORD-A1B2C3
TIME       : 2026-11-15 16:00:00 UTC
CALL SID   : CA1234567890abcdef
PHONE      : +919999999999
ITEMS:
  - 2x Classic Smash Burger (double) [extra cheese] = 500.00
  - 1x Peri Peri Fries (large) [standard] = 130.00
SUBTOTAL   : 630.00
NOTES      : None
========================================
```

### Switching to the Restaurant's Live POS API

When the restaurant provides their REST API endpoint, swap `TextFileOrderSink` for `HttpApiOrderSink` in `src/voice_agent/graph/tools.py`:

```python
from voice_agent.adapters.order_sink import HttpApiOrderSink, OrderSink

order_sink: OrderSink = HttpApiOrderSink(
    endpoint_url="[https://pos-api.restaurant.com/v1](https://pos-api.restaurant.com/v1)",
    api_key="YOUR_POS_API_KEY",
)
```

No changes to the LangGraph workflow, tools, or prompts are required.

---

## Tuning & Troubleshooting

* **Caller cut off mid-sentence:** Increase `endpointing` in `src/voice_agent/media/stt_tts.py` from `"450"` ms to `"650"` ms if callers pause frequently while reading a menu.
* **Menu items misheard by STT:** Add common phonetic misrecognitions to the item's `aliases` list in `data/menu.json`; `MenuCatalog.get_stt_keywords()` automatically boosts them in Deepgram.
* **Transfer not connecting:** Ensure `HUMAN_TRANSFER_NUMBER` is formatted in E.164 format (`+<country_code><number>`) and that your Twilio account has geographic permissions enabled for that destination.
