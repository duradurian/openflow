import base64
import io
import struct

import pytest

from app.config import Settings
from app.worker_protocol import MAX_AUDIO_BYTES, ProtocolError, read_record, validate_command, write_record


def envelope(message_type: str, **fields):
    return {"protocolVersion": 1, "type": message_type, "sequence": 0, **fields}


def test_framing_round_trip() -> None:
    stream = io.BytesIO()
    write_record(stream, {"protocolVersion": 1, "type": "hello", "sequence": 0})
    stream.seek(0)
    assert read_record(stream) == {"protocolVersion": 1, "type": "hello", "sequence": 0}
    assert read_record(stream) is None


def test_framing_rejects_truncated_and_oversized_records() -> None:
    with pytest.raises(ProtocolError, match="truncated"):
        read_record(io.BytesIO(struct.pack(">I", 10) + b"{}"))
    with pytest.raises(ProtocolError, match="invalid record length"):
        read_record(io.BytesIO(struct.pack(">I", 0)))


def test_audio_validation_rejects_oversized_or_odd_pcm() -> None:
    common = {"sessionId": "s", "generation": 1}
    oversized = base64.b64encode(b"x" * (MAX_AUDIO_BYTES + 1)).decode()
    with pytest.raises(ProtocolError, match="exceeds"):
        validate_command(envelope("audio", audioBase64=oversized, **common), Settings())
    odd = base64.b64encode(b"x").decode()
    with pytest.raises(ProtocolError, match="PCM16"):
        validate_command(envelope("audio", audioBase64=odd, **common), Settings())


def test_start_validation_normalizes_session_id() -> None:
    command = validate_command(
        envelope("start", sessionId="session", generation=2, sample_rate=16000, channels=1,
                 format="pcm_s16le", language="en", mode="fast"),
        Settings(),
    )
    assert command["start"].session_id == "session"
