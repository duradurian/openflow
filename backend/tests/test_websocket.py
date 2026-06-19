import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main as main_module
from app.audio import float32_to_pcm16
from app.main import app, settings as app_settings
from app.schemas import TranscriptSegment


class FakeSocketTranscriber:
    model_loaded = True
    model_source = "fake-model"

    def transcribe(self, audio, sample_rate, language, mode):
        return [TranscriptSegment(id="fake", start=0.0, end=len(audio) / sample_rate, text="socket text")]


@pytest.fixture
def websocket_client(monkeypatch):
    monkeypatch.setattr(main_module, "transcriber", FakeSocketTranscriber())
    monkeypatch.setattr(app_settings, "REQUIRE_API_TOKEN", False)
    monkeypatch.setattr(app_settings, "API_TOKEN", None)
    return TestClient(app)


def connect(client):
    return client.websocket_connect(
        "/v1/transcribe",
        headers={"host": "127.0.0.1:8000", "origin": "file://"},
    )


def start_message(**overrides):
    message = {
        "type": "start",
        "session_id": "test-session",
        "sample_rate": 16000,
        "channels": 1,
        "format": "pcm_s16le",
        "language": "en",
        "mode": "fast",
    }
    message.update(overrides)
    return message


def test_websocket_rejects_invalid_json(websocket_client) -> None:
    with connect(websocket_client) as websocket:
        websocket.send_text("{")
        event = websocket.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "INVALID_JSON"


def test_websocket_rejects_audio_before_start(websocket_client) -> None:
    with connect(websocket_client) as websocket:
        websocket.send_bytes(b"\x00\x00")
        event = websocket.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "MISSING_START"


def test_websocket_rejects_duplicate_start(websocket_client) -> None:
    with connect(websocket_client) as websocket:
        websocket.send_json(start_message())
        assert websocket.receive_json()["type"] == "ready"
        assert websocket.receive_json()["status"] == "listening"

        websocket.send_json(start_message())
        event = websocket.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "INVALID_MESSAGE"


def test_websocket_invalid_pcm_frame_is_reported(websocket_client) -> None:
    with connect(websocket_client) as websocket:
        websocket.send_json(start_message())
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_bytes(b"\x00")
        event = websocket.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "INVALID_AUDIO_FRAME"


def test_websocket_emits_transcribing_before_final(websocket_client) -> None:
    with connect(websocket_client) as websocket:
        websocket.send_json(start_message())
        websocket.receive_json()
        websocket.receive_json()

        speech = np.full(16000 // 2, 0.05, dtype=np.float32)
        silence = np.zeros(16000, dtype=np.float32)
        websocket.send_bytes(float32_to_pcm16(speech))
        websocket.send_bytes(float32_to_pcm16(silence))

        events = [websocket.receive_json() for _ in range(4)]
        assert any(event.get("status") == "speech_started" for event in events)
        assert any(event.get("status") == "transcribing" for event in events)
        assert any(event.get("type") == "final" for event in events)


def test_websocket_accepts_api_token_in_start_message(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "transcriber", FakeSocketTranscriber())
    monkeypatch.setattr(app_settings, "REQUIRE_API_TOKEN", True)
    monkeypatch.setattr(app_settings, "API_TOKEN", "secret")
    client = TestClient(app)

    with connect(client) as websocket:
        websocket.send_json(start_message(api_token="secret"))
        assert websocket.receive_json()["type"] == "ready"
        assert websocket.receive_json()["status"] == "listening"


def test_websocket_rejects_missing_start_api_token(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "transcriber", FakeSocketTranscriber())
    monkeypatch.setattr(app_settings, "REQUIRE_API_TOKEN", True)
    monkeypatch.setattr(app_settings, "API_TOKEN", "secret")
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect):
        with connect(client) as websocket:
            websocket.send_json(start_message(api_token="wrong"))
            event = websocket.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "UNAUTHORIZED"
            websocket.receive_json()
