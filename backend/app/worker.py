"""Stdio worker hosting transport-neutral transcription sessions."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, BinaryIO, Callable

from app.config import Settings, get_settings
from app.session import TranscriptionSession, TranscriberProtocol
from app.transcriber import WhisperTranscriber
from app.worker_protocol import ProtocolError, event, read_record, validate_command, write_record

logger = logging.getLogger(__name__)
MAX_QUEUED_AUDIO_BYTES = 512 * 1024


@dataclass
class ActiveSession:
    session: TranscriptionSession
    generation: int
    last_sequence: int
    canceled: bool = False


class TranscriptionWorker:
    def __init__(
        self,
        settings: Settings,
        transcriber: TranscriberProtocol,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.settings = settings
        self.transcriber = transcriber
        self.emit = emit
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TRANSCRIPTIONS)
        self.active: ActiveSession | None = None
        self.model_state = "ready" if getattr(transcriber, "model_loaded", False) else "loading"
        self._shutdown = False
        self._audio_tasks: set[asyncio.Task[None]] = set()
        self._scheduled_audio_bytes = 0
        self._audio_processing_lock = asyncio.Lock()

    async def load_model(self) -> None:
        await self.emit(event("model_state", state="loading"))
        try:
            await asyncio.to_thread(self.transcriber.load)  # type: ignore[attr-defined]
        except Exception as exc:
            self.model_state = "unavailable"
            logger.exception("Worker model load failed")
            await self.emit(event("model_state", state="unavailable", message=str(exc)))
        else:
            self.model_state = "ready"
            await self.emit(event("model_state", state="ready"))

    async def handle(self, raw: dict[str, Any]) -> None:
        try:
            command = validate_command(raw, self.settings)
        except ProtocolError as exc:
            await self.emit(event("error", code="INVALID_MESSAGE", message=str(exc)))
            return
        command_type = command["type"]
        if command_type == "hello":
            await self.emit(event("worker_ready", modelState=self.model_state))
        elif command_type == "start":
            await self._start(command)
        elif command_type == "audio":
            await self._audio(command)
        elif command_type == "stop":
            await self._stop(command)
        elif command_type == "cancel":
            await self._cancel(command)
        elif command_type == "shutdown":
            self._shutdown = True
            await self.emit(event("shutdown_ack"))

    def schedule_audio(self, raw: dict[str, Any]) -> None:
        """Schedule inference without blocking command intake (notably cancel)."""
        # Base64 is at most four thirds of raw PCM.  Reserve by the conservative
        # encoded size before scheduling, so stdin cannot create an unbounded task
        # or memory queue while inference is busy.
        encoded = raw.get("audioBase64")
        estimated_bytes = len(encoded) if isinstance(encoded, str) else MAX_QUEUED_AUDIO_BYTES + 1
        if self._scheduled_audio_bytes + estimated_bytes > MAX_QUEUED_AUDIO_BYTES * 2:
            task = asyncio.create_task(self._error(raw, "BACKPRESSURE", "Worker audio queue is full."))
        else:
            self._scheduled_audio_bytes += estimated_bytes

            async def run() -> None:
                try:
                    await self.handle(raw)
                finally:
                    self._scheduled_audio_bytes -= estimated_bytes

            task = asyncio.create_task(run())
        self._audio_tasks.add(task)
        task.add_done_callback(self._audio_tasks.discard)

    async def drain_audio(self) -> None:
        """Finish accepted audio before cooperative stop/shutdown."""
        if self._audio_tasks:
            await asyncio.gather(*tuple(self._audio_tasks), return_exceptions=True)

    async def _start(self, command: dict[str, Any]) -> None:
        if self.model_state != "ready":
            await self._error(command, "MODEL_UNAVAILABLE", "Transcription model is not loaded.")
            return
        if self.active:
            await self._error(command, "INVALID_MESSAGE", "A session is already active.")
            return
        start = command["start"]
        self.active = ActiveSession(
            session=TranscriptionSession(
                session_id=start.session_id, sample_rate=start.sample_rate, channels=start.channels,
                language=start.language, mode=start.mode, settings=self.settings,
                transcriber=self.transcriber, semaphore=self.semaphore,
            ),
            generation=command["generation"], last_sequence=command["sequence"],
        )
        await self.emit(event("accepted", sessionId=start.session_id, generation=command["generation"],
                              sequence=command["sequence"], acceptedBytes=0,
                              creditBytes=MAX_QUEUED_AUDIO_BYTES))
        await self._emit_session_events([
            {"type": "ready", "session_id": start.session_id, "model": self.settings.MODEL_NAME,
             "sample_rate": self.settings.SAMPLE_RATE}, {"type": "status", "status": "listening"}
        ])

    async def _audio(self, command: dict[str, Any]) -> None:
        active = self._matching_active(command)
        if not active:
            await self._error(command, "MISSING_START", "Send start before audio frames.")
            return
        if command["sequence"] <= active.last_sequence:
            await self._error(command, "INVALID_MESSAGE", "Audio sequence is not monotonic.")
            return
        active.last_sequence = command["sequence"]
        audio = command["audio"]
        if len(audio) > MAX_QUEUED_AUDIO_BYTES:
            await self._error(command, "INVALID_AUDIO_FRAME", "Audio frame exceeds worker queue limit.")
            return
        try:
            # Command intake remains concurrent so cancel can be received, but
            # TranscriptionSession itself has mutable VAD/buffer state and must
            # process accepted frames in order.
            async with self._audio_processing_lock:
                events = await active.session.accept_pcm16(audio)
        except ValueError as exc:
            await self._error(command, "INVALID_AUDIO_FRAME", str(exc))
            return
        except Exception as exc:
            logger.exception("Worker session failed")
            await self._error(command, "INFERENCE_FAILURE", str(exc))
            return
        if not active.canceled:
            await self._emit_session_events(events)
            await self.emit(event("accepted", sessionId=active.session.session_id, generation=active.generation,
                                  sequence=command["sequence"], acceptedBytes=len(audio),
                                  creditBytes=MAX_QUEUED_AUDIO_BYTES))

    async def _stop(self, command: dict[str, Any]) -> None:
        active = self._matching_active(command)
        if not active:
            await self._emit_stopped(command)
            return
        try:
            await self.drain_audio()
            events = await active.session.stop()
            if not active.canceled:
                await self._emit_session_events(events)
                await self.emit(event("stopped", sessionId=active.session.session_id,
                                      generation=active.generation, sequence=command["sequence"]))
        finally:
            self.active = None

    async def _cancel(self, command: dict[str, Any]) -> None:
        active = self._matching_active(command)
        if active:
            active.canceled = True
            self.active = None
        await self.emit(event("canceled", sessionId=command["sessionId"], generation=command["generation"],
                              sequence=command["sequence"]))

    def _matching_active(self, command: dict[str, Any]) -> ActiveSession | None:
        if not self.active:
            return None
        if (self.active.session.session_id, self.active.generation) != (command["sessionId"], command["generation"]):
            return None
        return self.active

    async def _emit_session_events(self, events: list[dict[str, Any]]) -> None:
        if not self.active or self.active.canceled:
            return
        for item in events:
            payload = dict(item)
            if "session_id" in payload:
                payload["sessionId"] = payload.pop("session_id")
            else:
                payload["sessionId"] = self.active.session.session_id
            payload["generation"] = self.active.generation
            await self.emit(event(payload.pop("type"), **payload))

    async def _emit_stopped(self, command: dict[str, Any]) -> None:
        await self.emit(event("status", sessionId=command["sessionId"], generation=command["generation"], status="stopped"))

    async def _error(self, command: dict[str, Any], code: str, message: str) -> None:
        fields = {key: command[key] for key in ("sessionId", "generation", "sequence") if key in command}
        await self.emit(event("error", code=code, message=message, **fields))


async def run_worker(stdin: BinaryIO = sys.stdin.buffer, stdout: BinaryIO = sys.stdout.buffer) -> None:
    write_lock = asyncio.Lock()

    async def emit(record: dict[str, Any]) -> None:
        async with write_lock:
            await asyncio.to_thread(write_record, stdout, record)

    settings = get_settings()
    transcriber = WhisperTranscriber(settings)
    worker = TranscriptionWorker(settings, transcriber, emit)
    await emit(event("worker_ready", modelState="loading"))
    load_task = asyncio.create_task(worker.load_model())
    try:
        while not worker._shutdown:
            try:
                record = await asyncio.to_thread(read_record, stdin)
            except ProtocolError as exc:
                logger.error("Protocol corruption: %s", exc)
                break
            if record is None:
                break
            # Audio inference is serialized by TranscriptionSession's semaphore,
            # but command intake remains live so cancel can suppress stale output.
            if record.get("type") == "audio":
                worker.schedule_audio(record)
            else:
                await worker.handle(record)
    finally:
        await worker.drain_audio()
        if not load_task.done():
            load_task.cancel()
        await asyncio.gather(load_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
