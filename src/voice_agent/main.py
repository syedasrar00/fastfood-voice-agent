"""FastAPI WebSocket server and Twilio call-transfer integration."""

import asyncio
import base64
import json
import uuid
from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Connect, Dial, Stream, VoiceResponse

from voice_agent.config import get_settings
from voice_agent.graph.tools import catalog
from voice_agent.graph.workflow import stream_spoken_sentences, voice_graph
from voice_agent.media.stt_tts import CartesiaTTSStreamer, DeepgramStreamer

settings = get_settings()
app = FastAPI(title="Fast-Food Voice Ordering Agent")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "restaurant": catalog.restaurant_name, "menu_items": len(catalog.items)}


@app.post("/voice/incoming")
async def incoming_call(
    CallSid: str = Form(...),
    From: str = Form(default="unknown"),
    Host: str = Form(default=""),
) -> Response:
    """Twilio webhook invoked when a customer dials the outlet number."""
    resp = VoiceResponse()
    connect = Connect()
    stream = Stream(url=f"wss://{Host}/voice/ws" if Host else "wss://YOUR_NGROK_DOMAIN/voice/ws")
    stream.parameter(name="call_sid", value=CallSid)
    stream.parameter(name="caller_phone", value=From)
    connect.append(stream)
    resp.append(connect)
    return Response(content=str(resp), media_type="application/xml")


def trigger_twilio_transfer(call_sid: str) -> None:
    """Redirect live call to a human counter/manager phone number."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.human_transfer_number):
        return
    client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
    twiml = VoiceResponse()
    twiml.append(Dial(settings.human_transfer_number))
    client.calls(call_sid).update(twiml=str(twiml))


@app.websocket("/voice/ws")
async def voice_websocket(ws: WebSocket) -> None:
    await ws.accept()
    stream_sid = ""
    call_sid = f"CALL-{uuid.uuid4().hex[:8]}"
    caller_phone = "unknown"
    active_response_task: asyncio.Task | None = None
    tts = CartesiaTTSStreamer(settings.cartesia_api_key, settings.cartesia_voice_id)

    async def send_mulaw_b64(b64_payload: str) -> None:
        if stream_sid:
            await ws.send_text(
                json.dumps({"event": "media", "streamSid": stream_sid, "media": {"payload": b64_payload}})
            )

    async def handle_barge_in() -> None:
        nonlocal active_response_task
        if active_response_task and not active_response_task.done():
            active_response_task.cancel()
        if stream_sid:
            await ws.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))

    async def respond_to_caller(text: str) -> None:
        ctx_id = uuid.uuid4().hex
        async for sentence in stream_spoken_sentences(text, call_sid=call_sid, caller_phone=caller_phone):
            await tts.synthesize_sentence(sentence, context_id=ctx_id, on_audio_b64=send_mulaw_b64)

        state = await voice_graph.aget_state({"configurable": {"thread_id": call_sid}})
        if state.values.get("transfer_requested"):
            await asyncio.sleep(1.2)
            await asyncio.to_thread(trigger_twilio_transfer, call_sid)

    async def on_transcript(transcript: str) -> None:
        nonlocal active_response_task
        if active_response_task and not active_response_task.done():
            active_response_task.cancel()
        active_response_task = asyncio.create_task(respond_to_caller(transcript))

    stt = DeepgramStreamer(
        api_key=settings.deepgram_api_key,
        keywords=catalog.get_stt_keywords(),
        on_final_transcript=on_transcript,
        on_speech_started=handle_barge_in,
    )

    try:
        await stt.connect()
        while True:
            raw = await ws.receive_text()
            event = json.loads(raw)
            kind = event.get("event")

            if kind == "start":
                start_meta = event["start"]
                stream_sid = start_meta["streamSid"]
                params = start_meta.get("customParameters", {})
                call_sid = params.get("call_sid", start_meta.get("callSid", call_sid))
                caller_phone = params.get("caller_phone", "unknown")
                greeting = f"Welcome to {catalog.restaurant_name}! What can I get started for you today?"
                active_response_task = asyncio.create_task(
                    tts.synthesize_sentence(greeting, context_id="greeting", on_audio_b64=send_mulaw_b64)
                )
            elif kind == "media":
                chunk = base64.b64decode(event["media"]["payload"])
                await stt.send_audio(chunk)
            elif kind == "stop":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if active_response_task and not active_response_task.done():
            active_response_task.cancel()
        await stt.close()