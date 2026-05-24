"""Piper-backed text-to-speech for slackbeatz.

Synthesises a phrase to a WAV file that the sampler can load. We use
`Piper <https://github.com/rhasspy/piper>`_ because it's:

* small (50-100 MB models)
* fast (~real-time on CPU, no GPU)
* local (no cloud round-trips)
* covers soft / breathy voices that suit slackbeatz's
  meditation-instructor use case.

Two execution paths, tried in order:

1. ``import piper`` Python module (preferred — no subprocess).
2. ``piper`` CLI on the PATH (fallback for Python versions where the
   pip dep doesn't bootstrap).

Either way, the synthesised audio lands in a cached file under
``~/Library/Caches/slackbeatz/tts/``. The cache key incorporates
``(text, voice, post_fx)`` so re-synthesising the same phrase is a
zero-cost lookup.

Voice models are downloaded on first use from the rhasspy/piper-voices
HuggingFace repo into
``~/Library/Application Support/slackbeatz/piper-voices/``. Models
already present on disk are reused; ``download_voice`` is the explicit
entry point if a caller wants to pre-warm before synthesis.

Post-FX (lowpass + reverb for a meditation-studio feel) lands in
issue #30 — for now ``post_fx=True`` is honoured by the cache key
but otherwise a no-op.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional


# Default voice — soft female English. Good general-purpose
# meditation-instructor texture.
DEFAULT_VOICE = "en_US-amy-low"


# --------------------------------------------------------------------------
# Per-platform paths
# --------------------------------------------------------------------------

def _voices_dir() -> Path:
    """Where downloaded Piper ``.onnx`` voice files live."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "slackbeatz"
    else:
        base = Path.home() / ".local" / "share" / "slackbeatz"
    return base / "piper-voices"


def _cache_dir() -> Path:
    """Where synthesised WAVs are cached."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "slackbeatz"
    else:
        base = Path.home() / ".cache" / "slackbeatz"
    return base / "tts"


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------
# Voice model discovery + download
# --------------------------------------------------------------------------

def _voice_paths(voice: str) -> tuple[Path, Path]:
    """Return the ``(model, config)`` paths for *voice*. They may not
    exist yet — :func:`download_voice` creates them."""
    d = _voices_dir()
    return d / f"{voice}.onnx", d / f"{voice}.onnx.json"


def _voice_url(voice: str) -> tuple[str, str]:
    """Map a voice name like ``en_US-amy-low`` to the HuggingFace URLs
    for its model + config. Format:

        en_US-amy-low → en/en_US/amy/low/en_US-amy-low.onnx{,.json}

    The repo follows that lang/locale/speaker/quality convention for
    every entry, so this purely-structural mapping covers any voice
    without a hardcoded table.
    """
    parts = voice.split("-")
    if len(parts) < 3:
        raise ValueError(
            f"voice name {voice!r} doesn't match the "
            f"<locale>-<speaker>-<quality> Piper convention"
        )
    locale = parts[0]                       # en_US
    speaker = "-".join(parts[1:-1])         # amy (or amy-medium → amy)
    quality = parts[-1]                     # low / medium / high / x_low
    lang = locale.split("_")[0]             # en
    base = (
        f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        f"{lang}/{locale}/{speaker}/{quality}/{voice}"
    )
    return base + ".onnx", base + ".onnx.json"


def available_voices() -> list[str]:
    """List voice names that already have model + config on disk."""
    d = _voices_dir()
    if not d.is_dir():
        return []
    out: list[str] = []
    for p in sorted(d.glob("*.onnx")):
        cfg = p.with_suffix(p.suffix + ".json")
        if cfg.is_file():
            out.append(p.stem)
    return out


def download_voice(voice: str) -> None:
    """Fetch *voice*'s model + config from rhasspy/piper-voices into
    :func:`_voices_dir`. Idempotent — if both files already exist, no
    work is done."""
    model_path, config_path = _voice_paths(voice)
    if model_path.is_file() and config_path.is_file():
        return
    _ensure_dir(_voices_dir())
    model_url, config_url = _voice_url(voice)
    print(f"slackbeatz tts: downloading {voice} ({model_url})", file=sys.stderr)
    for src_url, dest in ((model_url, model_path), (config_url, config_path)):
        if dest.is_file():
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with urllib.request.urlopen(src_url, timeout=60) as resp, \
                    tmp.open("wb") as out:
                shutil.copyfileobj(resp, out)
            tmp.rename(dest)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"failed to download Piper voice {voice} from {src_url}: {e}"
            ) from e


# --------------------------------------------------------------------------
# Cache + synthesis driver
# --------------------------------------------------------------------------

def _cache_key(text: str, voice: str, post_fx: bool) -> str:
    """Short hash that uniquely identifies a synthesis request. The
    inputs and a stable separator are concatenated then SHA-256'd; we
    keep the first 16 hex chars (plenty unique for ~10⁶ entries)."""
    h = hashlib.sha256(f"{text}|{voice}|{int(bool(post_fx))}".encode("utf-8"))
    return h.hexdigest()[:16]


def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    *,
    output_path: Optional[Path] = None,
    post_fx: bool = True,
) -> Path:
    """Synthesise *text* via Piper to a WAV file. Cached by
    ``(text, voice, post_fx)``.

    Parameters
    ----------
    text:
        The phrase to speak. Single-line strings work best — long
        paragraphs get rendered fine but the sampler treats the whole
        WAV as one note, so consider splitting at sentence boundaries
        for finer control.
    voice:
        A Piper voice name (e.g. ``en_US-amy-low``). Downloaded on
        first use if not already cached.
    output_path:
        Optional explicit destination. If omitted, the cache directory
        is used.
    post_fx:
        Reserved for issue #30 (lowpass + reverb meditation chain).
        Honoured by the cache key today; the actual FX chain lands in
        #30.

    Returns
    -------
    Path
        Where the synthesised WAV lives on disk. May be the cache path
        (default) or *output_path* if explicitly provided.
    """
    if not text:
        raise ValueError("synthesize(): text must be a non-empty string")

    cache_path = _ensure_dir(_cache_dir()) / f"{_cache_key(text, voice, post_fx)}.wav"
    target = Path(output_path) if output_path is not None else cache_path

    if target.is_file() and target.stat().st_size > 0:
        return target

    download_voice(voice)
    model_path, config_path = _voice_paths(voice)

    # Synthesise into a temp file first, then move into place atomically
    # so a partial WAV can't leave a corrupt cache entry.
    tmp_path = target.with_suffix(target.suffix + ".part")
    try:
        _run_piper(text, model_path, config_path, tmp_path)
        # post-FX hook is wired in by issue #30. Until then this
        # is a no-op even when post_fx=True.
        tmp_path.replace(target)
    finally:
        tmp_path.unlink(missing_ok=True)

    return target


# --------------------------------------------------------------------------
# Piper backends
# --------------------------------------------------------------------------

def _run_piper(
    text: str, model_path: Path, config_path: Path, output: Path,
) -> None:
    """Run Piper for one phrase. Tries the Python module first; falls
    back to the ``piper`` binary on PATH."""
    # Path 1: in-process Python module. Avoids subprocess overhead.
    try:
        from piper.voice import PiperVoice  # type: ignore[import]
    except ImportError:
        PiperVoice = None
    if PiperVoice is not None:
        try:
            import wave
            voice_obj = PiperVoice.load(str(model_path), config_path=str(config_path))
            with wave.open(str(output), "wb") as wav_file:
                voice_obj.synthesize(text, wav_file)
            return
        except Exception as e:
            # Module exists but failed (e.g. version mismatch) — fall
            # through to the CLI path with a one-line note. Don't spam
            # on every call: a single stderr line is enough.
            print(
                f"slackbeatz tts: in-process Piper failed ({e}); "
                f"trying piper CLI", file=sys.stderr,
            )

    # Path 2: ``piper`` CLI on PATH.
    piper_bin = shutil.which("piper")
    if piper_bin is None:
        raise RuntimeError(
            "Piper isn't installed. Install with one of:\n"
            "  pip install piper-tts            (Python module)\n"
            "  brew install piper-tts           (CLI binary)\n"
        )
    proc = subprocess.run(
        [
            piper_bin,
            "--model", str(model_path),
            "--config", str(config_path),
            "--output_file", str(output),
        ],
        input=text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"piper CLI exited {proc.returncode}: {err or '(no stderr)'}"
        )


