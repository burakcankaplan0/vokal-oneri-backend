from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict, Any
import requests
import tempfile
import os
import numpy as np

import librosa

app = FastAPI(title="Vocal Match Backend", version="0.1.0")
print(f"API_KEY present: {bool(os.getenv('API_KEY', '').strip())}")


# -----------------------------
# Models
# -----------------------------
class Song(BaseModel):
    id: str
    title: str
    artist: Optional[str] = ""
    link: Optional[str] = ""
    minNote: int  # MIDI integer (e.g., 48)
    maxNote: int  # MIDI integer (e.g., 67)


class AnalyzeRequest(BaseModel):
    audio_url: HttpUrl
    songs: List[Song]
    user_is_premium: bool = False


# -----------------------------
# Helpers
# -----------------------------
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_name(midi: int) -> str:
    octave = (midi // 12) - 1
    return f"{NOTE_NAMES[midi % 12]}{octave}"


def safe_extension_from_url(url: str) -> str:
    """
    Try to keep a usable extension so audioread/librosa can decode better.
    """
    lowered = url.lower()
    for ext in [".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm", ".mp4"]:
        if lowered.endswith(ext):
            return ext
    return ".audio"


def download_to_temp_file(audio_url: str) -> str:
    """
    Downloads audio to a temp file and returns file path.
    """
    try:
        resp = requests.get(str(audio_url), timeout=45)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Audio download error: {e}")

    if resp.status_code != 200 or not resp.content:
        raise HTTPException(status_code=400, detail="Audio download failed or empty content")

    ext = safe_extension_from_url(str(audio_url))
    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)

    with open(path, "wb") as f:
        f.write(resp.content)

    return path


def estimate_user_range_midi(y: np.ndarray, sr: int) -> Dict[str, Any]:
    """
    Extract pitch with librosa.pyin and estimate a "comfortable" range
    using robust percentiles.
    """
    # pyin returns f0 per frame; NaN when unvoiced.
    f0, voiced_flag, voiced_prob = librosa.pyin(
        y,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C6"),
    )

    voiced_f0 = f0[~np.isnan(f0)]
    if voiced_f0.size < 30:
        raise HTTPException(
            status_code=400,
            detail="Not enough voiced audio detected. Record longer and louder (e.g., 30–45s).",
        )

    # Robust range: 10th to 90th percentile
    low_hz = float(np.percentile(voiced_f0, 10))
    high_hz = float(np.percentile(voiced_f0, 90))

    low_midi = int(round(librosa.hz_to_midi(low_hz)))
    high_midi = int(round(librosa.hz_to_midi(high_hz)))

    if high_midi <= low_midi:
        raise HTTPException(status_code=400, detail="Range estimation failed (bad audio)")

    # Optional: stability score (rough)
    # compute frame-to-frame jitter on voiced frames
    diffs = np.diff(voiced_f0)
    jitter = float(np.median(np.abs(diffs))) if diffs.size else 0.0

    return {
        "low_midi": low_midi,
        "high_midi": high_midi,
        "low_name": midi_to_name(low_midi),
        "high_name": midi_to_name(high_midi),
        "jitter": jitter,
    }


def score_song(user_low: int, user_high: int, song_min: int, song_max: int) -> float:
    """
    Simple but effective scoring:
    - Reward overlap with user's comfortable range
    - Bonus if song is fully inside user's range
    - Penalize exceeding user's range
    """
    overlap = max(0, min(user_high, song_max) - max(user_low, song_min))
    song_len = max(1, song_max - song_min)
    user_len = max(1, user_high - user_low)

    inside_bonus = 0.25 if (song_min >= user_low and song_max <= user_high) else 0.0

    exceed = max(0, song_min - user_low) + max(0, song_max - user_high)
    exceed_penalty = min(1.0, exceed / 12.0)  # 12 semitone ~ 1 octave

    base = (overlap / song_len) * 0.7 + (overlap / user_len) * 0.3
    return float(base + inside_bonus - exceed_penalty)


# -----------------------------
# Endpoints
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/analyze")
def analyze(req: AnalyzeRequest, x_api_key: str = Header(default="", alias="X-API-KEY")):
    expected = os.getenv("API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="Server misconfigured: API_KEY is not set")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    temp_path = None
    try:
        # 1) download audio to file
        temp_path = download_to_temp_file(str(req.audio_url))

        # 2) load audio
        try:
            y, sr = librosa.load(temp_path, sr=22050, mono=True)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Audio decode failed. Ensure ffmpeg is installed in Docker. Error: {e}",
            )

        if y is None or len(y) < sr * 2:
            raise HTTPException(status_code=400, detail="Audio too short")

        # 3) estimate user range
        r = estimate_user_range_midi(y, sr)
        user_low = r["low_midi"]
        user_high = r["high_midi"]

        # 4) score songs
        scored = []
        for s in req.songs:
            sc = score_song(user_low, user_high, int(s.minNote), int(s.maxNote))
            scored.append((sc, s))

        if not scored:
            raise HTTPException(status_code=400, detail="No songs to score")

        scored.sort(key=lambda x: x[0], reverse=True)

        k = 10 if req.user_is_premium else 1
        best = scored[:k]

        return {
            "lowNoteMidi": user_low,
            "highNoteMidi": user_high,
            "lowNoteName": r["low_name"],
            "highNoteName": r["high_name"],
            "stabilityHint": "higher is steadier" if r["jitter"] else "n/a",
            "recommendations": [
                {
                    "id": s.id,
                    "title": s.title,
                    "artist": s.artist or "",
                    "link": s.link or "",
                    "minNote": int(s.minNote),
                    "maxNote": int(s.maxNote),
                    "score": float(sc),
                }
                for sc, s in best
            ],
        }

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
