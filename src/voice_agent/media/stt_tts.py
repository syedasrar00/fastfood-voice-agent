"""Streaming Deepgram STT and Cartesia Sonic TTS integration."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode
import websockets


class DeepgramStreamer:
    def __init__(
        self,
        api_key: str,
        keywords: list[str],
        on_final_transcript: Callable[[str], Awaitable[None]],
        on_speech_started: Callable[[], Awaitable[None]],
    ):
        self.api_key = api_key
        self.keywords = keywords[:50]
        self.on_final_transcript = on_final_transcript
        self.on_speech_started = on_speech_started
        self._ws = None
        self._recv_task: asyncio.Task | None = None

    async def connect(self) -> None:
        params = [
            ("model", "nova-2"),
            ("encoding", "mulaw"),
            ("sample_rate", "8000"),
            ("channels", "1"),
            ("punctuate", "true"),
            ("interim_results", "true"),
            ("vad_events", "true"),
            ("endpointing", "450"),
        ]
        for kw in self.keywords:
            params.append(("keywords", f"{kw}:2"))

        url = f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"
        self._ws = await websockets.connect(url, additional_headers={"Authorization": f"Token {self.api_key}"})
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        if self._ws:
            await self._ws.send(mulaw_bytes)

    async def _receive_loop(self) -> None:
        transcript_parts: list[str] = []
        async for raw in self._ws:
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "SpeechStarted":
                await self.on_speech_started()
            elif msg_type == "Results":
                alt = data.get("channel", {}).get("alternatives", [{}])[0]
                text = alt.get("transcript", "").strip()
                if data.get("is_final") and text:
                    transcript_parts.append(text)
                if data.get("speech_final") and transcript_parts:
                    full_utterance = " ".join(transcript_parts)
                    transcript_parts.clear()
                    await self.on_final_transcript(full_utterance)

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()


class CartesiaTTSStreamer:
    def __init__(self, api_key: str, voice_id: str):
        self.url = f"wss://api.cartesia.ai/tts/websocket?api_key={api_key}&cartesia_version=2024-06-10"
        self.voice_id = voice_id

    async def synthesize_sentence(self, text: str, context_id: str, on_audio_b64: Callable[[str], Awaitable[None]]) -> None:
        async with websockets.connect(self.url) as ws:
            payload = {
                "model_id": "sonic-2",
                "transcript": text,
                "voice": {"mode": "id", "id": self.voice_id},
                "output_format": {"container": "raw", "encoding": "pcm_mulaw", "sample_rate": 8000},
                "context_id": context_id,
                "continue": False,
            }
            await ws.send(json.dumps(payload))
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "chunk" and msg.get("data"):
                    await on_audio_b64(msg["data"])
                elif msg.get("done"):
                    break