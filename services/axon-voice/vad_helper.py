"""Lightweight VAD helpers used by the Axon Voice ambient listener.

Provides a small abstraction over webrtcvad when available and falls back to
an energy-based heuristic so ambient mode degrades gracefully on systems
without webrtcvad installed. Pure stdlib + optional webrtcvad so it is
unit-testable.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

try:
    import webrtcvad

    HAVE_WEBRTC = True
except Exception:
    HAVE_WEBRTC = False


def _rms_from_pcm(pcm_bytes: bytes) -> float:
    # 16-bit signed little-endian samples
    if not pcm_bytes:
        return 0.0
    count = len(pcm_bytes) // 2
    if count == 0:
        return 0.0
    fmt = f"<{count}h"
    try:
        samples = struct.unpack(fmt, pcm_bytes[: count * 2])
    except struct.error:
        return 0.0
    ssum = sum(s * s for s in samples)
    return math.sqrt(ssum / count)


def is_speech_wav(path: str | Path, sample_rate: int = 16000) -> bool:
    """Return True when the given WAV or raw PCM file likely contains speech.

    If webrtcvad is installed it will be used (aggressive mode). Otherwise a
    simple RMS amplitude threshold is applied. This function expects 16-bit
    mono PCM at the given sample_rate; it tolerates a WAV header by scanning
    for the first 1000 bytes for non-null patterns.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return False

    # If WAV header present, try to find the data chunk start (simple heuristic)
    if raw[:4] == b"RIFF" and b"data" in raw[:2048]:
        idx = raw.find(b"data")
        if idx != -1 and idx + 8 < len(raw):
            pcm = raw[idx + 8 :]
        else:
            pcm = raw
    else:
        pcm = raw

    # Prefer webrtcvad when available
    if HAVE_WEBRTC:
        try:
            vad = webrtcvad.Vad(2)  # moderate aggressiveness
            # webrtcvad expects 10/20/30ms frames; use 30ms frames here
            frame_ms = 30
            bytes_per_frame = int(sample_rate * (frame_ms / 1000.0) * 2)
            for i in range(0, len(pcm) - bytes_per_frame + 1, bytes_per_frame):
                frame = pcm[i : i + bytes_per_frame]
                if vad.is_speech(frame, sample_rate):
                    return True
            return False
        except Exception:
            # Fall back to RMS heuristic
            pass

    # RMS energy heuristic
    rms = _rms_from_pcm(pcm[: sample_rate * 2])  # first second
    return rms > 500.0
