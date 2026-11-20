"""Speech services. Wyoming (the protocol Home Assistant's speech servers speak: faster-whisper, Piper,
Kokoro and others) is built in; any other Pipecat STT or TTS service is reached by class path.

    stt: {provider: wyoming, uri: tcp://speech-host:10300, language: en}
    tts: {provider: wyoming, uri: tcp://speech-host:10200, voice: en_US-lessac-medium}
    tts: {provider: "pipecat.services.openai.tts:OpenAITTSService", api_key_env: OPENAI_API_KEY, settings: {...}}
"""
from __future__ import annotations

import asyncio
import importlib
import io
import os
import uuid
import wave
from collections.abc import AsyncGenerator

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame, TTSAudioRawFrame
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.tts import Synthesize, SynthesizeVoice

RATE, WIDTH, CHANNELS = 16000, 2, 1
CHUNK = 3200   # 100 ms at 16 kHz s16le


async def wyoming_transcribe(uri: str, pcm: bytes, language: str | None = None, timeout: float = 30) -> str:
    """Raw 16 kHz mono s16le -> text."""
    async def go() -> str:
        async with AsyncClient.from_uri(uri) as c:
            await c.write_event(Transcribe(language=language).event())
            await c.write_event(AudioStart(rate=RATE, width=WIDTH, channels=CHANNELS).event())
            for i in range(0, len(pcm), CHUNK):
                await c.write_event(AudioChunk(rate=RATE, width=WIDTH, channels=CHANNELS, audio=pcm[i:i + CHUNK]).event())
            await c.write_event(AudioStop().event())
            while (ev := await c.read_event()) is not None:
                if Transcript.is_type(ev.type):
                    return Transcript.from_event(ev).text.strip()
        return ""
    return await asyncio.wait_for(go(), timeout)


async def wyoming_synthesize(uri: str, text: str, voice: str | None = None,
                             timeout: float = 60) -> AsyncGenerator[tuple[bytes, int, int, int], None]:
    """Text -> (pcm, rate, width, channels) chunks as the server sends them."""
    async with AsyncClient.from_uri(uri) as c:
        await c.write_event(Synthesize(text=text, voice=SynthesizeVoice(name=voice) if voice else None).event())
        rate, width, ch = 22050, 2, 1
        while True:
            ev = await asyncio.wait_for(c.read_event(), timeout)
            if ev is None or AudioStop.is_type(ev.type):
                return
            if AudioStart.is_type(ev.type):
                s = AudioStart.from_event(ev)
                rate, width, ch = s.rate, s.width, s.channels
            elif AudioChunk.is_type(ev.type):
                a = AudioChunk.from_event(ev)
                yield a.audio, a.rate or rate, a.width or width, a.channels or ch


class WyomingSTTService(SegmentedSTTService):
    """Pipecat STT over Wyoming. Segmented: VAD cuts the speech, the server transcribes each segment."""

    def __init__(self, *, uri: str, language: str | None = None, **kwargs):
        super().__init__(settings=STTSettings(model=None, language=language), **kwargs)
        self._uri, self._language = uri, language

    @property
    def wants_wav_segments(self) -> bool:
        return False

    @property
    def segment_sample_rate(self) -> int:
        return RATE

    def can_generate_metrics(self) -> bool:
        return True

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        try:
            text = await wyoming_transcribe(self._uri, audio, self._language)
        except Exception as e:
            yield ErrorFrame(error=f"wyoming stt {self._uri}: {type(e).__name__}: {e}")
            return
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), self._language)


class WyomingTTSService(TTSService):
    """Pipecat TTS over Wyoming."""

    def __init__(self, *, uri: str, voice: str | None = None, **kwargs):
        super().__init__(push_start_frame=True, push_stop_frames=True,
                         settings=TTSSettings(model=None, voice=voice, language=None), **kwargs)
        self._uri = uri

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        try:
            async for pcm, rate, _, ch in wyoming_synthesize(self._uri, text, self._settings.voice):
                yield TTSAudioRawFrame(audio=pcm, sample_rate=rate, num_channels=ch, context_id=context_id)
        except Exception as e:
            yield ErrorFrame(error=f"wyoming tts {self._uri}: {type(e).__name__}: {e}")


def _load_class(path: str):
    module, _, name = path.partition(":")
    if not name:
        raise ValueError(f"provider {path!r}: give it as module.path:ClassName")
    return getattr(importlib.import_module(module), name)


def _build(spec: dict, kind: str):
    spec = dict(spec)
    provider = spec.pop("provider", "")
    if provider == "wyoming":
        cls = WyomingSTTService if kind == "stt" else WyomingTTSService
        return cls(**spec)
    if not provider:
        raise ValueError(f"{kind}: `provider` is required (wyoming, or module.path:ClassName)")
    cls = _load_class(provider)
    key_env = spec.pop("api_key_env", None)
    if key_env:
        spec["api_key"] = os.environ[key_env]
    settings = spec.pop("settings", None)
    if settings is not None and hasattr(cls, "Settings"):
        spec["settings"] = cls.Settings(**settings)
    return cls(**spec)


def build_stt(spec: dict):
    return _build(spec, "stt") if spec else None


def build_tts(spec: dict):
    return _build(spec, "tts") if spec else None


async def to_pcm16k(data: bytes) -> bytes:
    """Any audio container ffmpeg reads (wav, webm/opus, m4a, ogg, mp3) -> raw 16 kHz mono s16le."""
    p = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1",
        "-ar", str(RATE), "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate(data)
    if p.returncode:
        raise ValueError(f"ffmpeg could not decode the audio: {err.decode()[:300]}")
    return out


def pcm_to_wav(pcm: bytes, rate: int, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_of(pcm: bytes, rate: int = RATE) -> bytes:
    return pcm_to_wav(pcm, rate)


async def transcribe(stt, pcm: bytes) -> str:
    """One utterance through any Pipecat STT service, outside a pipeline (for the HTTP turn)."""
    if isinstance(stt, WyomingSTTService):
        return await wyoming_transcribe(stt._uri, pcm, stt._language)
    audio = pcm if not getattr(stt, "wants_wav_segments", True) else wav_of(pcm)
    parts = []
    async for frame in stt.run_stt(audio):
        if isinstance(frame, TranscriptionFrame):
            parts.append(frame.text)
        elif isinstance(frame, ErrorFrame):
            raise RuntimeError(frame.error)
    return " ".join(parts).strip()


async def synthesize(tts, text: str) -> tuple[bytes, int]:
    """Text through any Pipecat TTS service, outside a pipeline -> (pcm, rate)."""
    if isinstance(tts, WyomingTTSService):
        pcm, rate = bytearray(), RATE
        async for chunk, r, _, _ in wyoming_synthesize(tts._uri, text, tts._settings.voice):
            pcm += chunk
            rate = r
        return bytes(pcm), rate
    pcm, rate = bytearray(), None
    async for frame in tts.run_tts(text, uuid.uuid4().hex):
        if isinstance(frame, TTSAudioRawFrame):
            pcm += frame.audio
            rate = frame.sample_rate
        elif isinstance(frame, ErrorFrame):
            raise RuntimeError(frame.error)
    return bytes(pcm), rate or 24000
