from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Install a faster-whisper model for offline Openflow startup.")
    parser.add_argument("model", nargs="?", default="large-v3-turbo", help="Model name or Hugging Face repo id.")
    parser.add_argument("--models-dir", default="./models", help="Directory where local models are stored.")
    args = parser.parse_args()

    models_dir = Path(args.models_dir).expanduser().resolve()
    target = models_dir / args.model.replace("/", "__")
    target.mkdir(parents=True, exist_ok=True)

    try:
        from faster_whisper.utils import download_model
    except ImportError as exc:
        raise SystemExit(
            "faster-whisper is not installed. Run backend dependency setup before installing models."
        ) from exc

    print(f"Installing {args.model} into {target}")
    try:
        downloaded = download_model(args.model, output_dir=str(target), local_files_only=False)
    except TypeError:
        downloaded = download_model(args.model, cache_dir=str(models_dir), local_files_only=False)

    resolved = Path(downloaded).expanduser().resolve() if downloaded else target
    print("\nModel installed.")
    print("Add or keep these values in backend/.env:")
    print(f"MODEL_NAME={args.model}")
    print(f"MODEL_PATH={resolved}")
    print("ALLOW_MODEL_DOWNLOAD=false")


if __name__ == "__main__":
    main()
