"""Voice-note transcription (pluggable). Default "none": voice notes are stored
and the sender is asked for a short text; "faster_whisper" runs locally."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)
_model = None


def transcribe(data: bytes, mime: str) -> str | None:
    if settings.transcriber == "faster_whisper":
        try:
            from faster_whisper import WhisperModel  # optional dependency
        except ImportError:
            log.warning("CPM_TRANSCRIBER=faster_whisper but the package is not installed")
            return None
        global _model
        if _model is None:
            _model = WhisperModel("small", device="auto", compute_type="int8")
        suffix = ".ogg" if "ogg" in mime else ".m4a" if "mp4" in mime or "m4a" in mime else ".audio"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            path = f.name
        try:
            segments, _ = _model.transcribe(path)  # language auto-detected (Hindi/English/Marathi...)
            return " ".join(s.text.strip() for s in segments).strip() or None
        finally:
            Path(path).unlink(missing_ok=True)
    return None
