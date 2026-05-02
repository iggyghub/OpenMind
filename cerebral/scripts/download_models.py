"""
Download required models for the Cerebral audio pipeline and TTS engine.

  python cerebral/scripts/download_models.py

Downloads:
  - vosk-model-small-en-us-0.15  (~40 MB)  → cerebral/models/
  - Kokoro TTS model              (~330 MB) → HuggingFace cache (one-time)
"""

import io
import urllib.request
import zipfile
from pathlib import Path

MODELS_DIR = Path(__file__).parent.parent / "models"

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_DIR = "vosk-model-small-en-us-0.15"


def _progress_hook(count: int, block_size: int, total: int) -> None:
    if total <= 0:
        print(f"\r  {count * block_size // 1_048_576} MB downloaded...", end="", flush=True)
    else:
        pct = min(100, count * block_size * 100 // total)
        mb = count * block_size // 1_048_576
        total_mb = total // 1_048_576
        print(f"\r  {pct:3d}%  ({mb}/{total_mb} MB)", end="", flush=True)


def download_vosk() -> None:
    out_dir = MODELS_DIR / VOSK_DIR
    if out_dir.exists():
        print(f"[models] Vosk model already present at {out_dir}")
        return

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = MODELS_DIR / f"{VOSK_DIR}.zip"

    print(f"[models] Downloading Vosk small EN model (~40 MB) from:")
    print(f"         {VOSK_URL}")
    try:
        urllib.request.urlretrieve(VOSK_URL, zip_path, reporthook=_progress_hook)
    except Exception as exc:
        zip_path.unlink(missing_ok=True)
        raise RuntimeError(f"Download failed: {exc}") from exc
    print()  # newline after progress

    print("[models] Extracting...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(MODELS_DIR)
    zip_path.unlink()

    print(f"[models] Vosk model ready at {out_dir}")


def download_kokoro() -> None:
    """Warm-start the Kokoro pipeline so HuggingFace downloads the model now."""
    print("[models] Warming up Kokoro TTS (~330 MB, one-time HuggingFace download)...")
    try:
        from kokoro import KPipeline
        pipe = KPipeline(lang_code="a")
        # Generate one word to pull all weights — discard audio output
        for _ in pipe("hello", voice="af_heart"):
            break
        print("[models] Kokoro TTS model ready")
    except ImportError:
        print("[models] kokoro not installed — skipping (pip install kokoro soundfile)")
    except Exception as exc:
        print(f"[models] Kokoro warm-up failed: {exc}")


if __name__ == "__main__":
    download_vosk()
    download_kokoro()
