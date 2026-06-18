from __future__ import annotations

from pathlib import Path

from app.config import Settings


class ModelUnavailableError(RuntimeError):
    pass


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def expected_model_path(settings: Settings) -> Path:
    if settings.MODEL_PATH:
        return _resolve_path(settings.MODEL_PATH)
    return (_resolve_path(settings.MODELS_DIR) / settings.MODEL_NAME).resolve()


def resolve_model_source(settings: Settings) -> tuple[str, bool]:
    """Return the model source and whether faster-whisper may use network access."""
    if settings.MODEL_PATH:
        path = _resolve_path(settings.MODEL_PATH)
        if path.exists():
            return str(path), True
        raise ModelUnavailableError(
            f"Configured MODEL_PATH does not exist: {path}. "
            "Run backend/scripts/install_model.py or update MODEL_PATH."
        )

    path = expected_model_path(settings)
    if path.exists():
        return str(path), True

    if settings.ALLOW_MODEL_DOWNLOAD:
        return settings.MODEL_NAME, False

    raise ModelUnavailableError(
        f"Local Whisper model was not found at {path}. "
        f"Run `python scripts/install_model.py {settings.MODEL_NAME}` from backend/ "
        "or set MODEL_PATH to an existing faster-whisper model directory."
    )
