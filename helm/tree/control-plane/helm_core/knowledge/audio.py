"""Голос/видео → текст (§14.7, ADR-021): ffmpeg → Silero VAD → GigaAM.

GigaAM встроена в этот же процесс/образ (`helm-knowledge-worker`), не
отдельный сервис (в отличие от эмбеддингов, ADR-025) — спека прямо
характеризует её как "on-demand worker, not resident daemon model":
модель грузится и выгружается на каждый вызов, память не держится
между голосовыми job'ами (тот же процесс уже вызывается редко и
последовательно, `worker.py` разбирает job'ы строго по одному).

Импорты gigaam/torch/silero_vad — ВНУТРИ `transcribe_audio()`, не на
уровне модуля: тот же принцип, что уже есть у `parsers.py` для
markitdown/docling — модуль должен оставаться импортируемым (в т.ч. в
тестах) без установленных тяжёлых пакетов, только вызов самой функции
их требует.

Официальный `.transcribe_longform()` GigaAM не используется — он тянет
`pyannote.audio` для VAD, чья дефолтная модель сегментации
(`pyannote/segmentation-3.0`) gated на HuggingFace и потребовала бы
`/human`-задачу на каждый живой запуск (см. ADR-021). Вместо этого —
Silero VAD (MIT, не gated) для собственной сегментации на куски
`MAX_SEGMENT_SECONDS`, тот же жёсткий лимит (25с), под которым работает
`.transcribe()` без `[longform]`-экстры.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

#: Telegram voice message — всегда .ogg (mime audio/ogg), но владелец
#: может прислать голосовую заметку другим форматом или видео (§14.7
#: "Audio/video") — извлечение звуковой дорожки делает тот же ffmpeg.
AUDIO_EXTENSIONS = {".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a", ".flac",
                    ".mp4", ".mov", ".webm"}

#: ADR-021 — выбрана живым замером 31.08.2026: RSS практически
#: неотличим от трёх других кандидатов (одна и та же 220M-параметровая
#: архитектура), RNNT + пунктуация/нормализация текста — важно для
#: читаемости итогового Knowledge-источника.
MODEL_NAME = "e2e_rnnt"

#: `.transcribe()` без `[longform]` работает только до 25с (проверено
#: чтением исходника репозитория) — с запасом, не впритык к границе.
MAX_SEGMENT_SECONDS = 24.0

#: Веса GigaAM запекаются на сборке образа под этот путь (Dockerfile.
#: worker) — тот же приём, что HF_HOME у helm-embed (ADR-025): сборка
#: идёт от root, рантайм — от helm-worker, без общего явного пути кэш
#: со сборки был бы не виден процессу в рантайме.
GIGAAM_DOWNLOAD_ROOT = "/opt/helm/knowledge-worker/gigaam-models"


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def _convert_to_wav(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-af", "loudnorm",
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(dst)],
        check=True, capture_output=True,
    )


def _segment_boundaries(speech_timestamps: list[dict], total_seconds: float) -> list[tuple[float, float]]:
    """Объединить соседние VAD-сегменты речи в блоки ≤MAX_SEGMENT_SECONDS.

    Без речи вовсе (тишина/шум) — один блок на весь файл: `.transcribe()`
    на нём либо вернёт пустой/бессмысленный текст (обычный `_quality_ok()`
    gate в parsers.py отфильтрует это так же, как и для документов), не
    повод не пытаться вовсе.
    """
    if not speech_timestamps:
        return [(0.0, total_seconds)]
    boundaries: list[tuple[float, float]] = []
    block_start = speech_timestamps[0]["start"]
    block_end = speech_timestamps[0]["end"]
    for seg in speech_timestamps[1:]:
        if seg["end"] - block_start > MAX_SEGMENT_SECONDS:
            boundaries.append((block_start, block_end))
            block_start = seg["start"]
        block_end = seg["end"]
    boundaries.append((block_start, block_end))
    return boundaries


def transcribe_audio(path: Path) -> str:
    """Текст с таймкодами по сегментам (§14.7 "timestamped SOURCE")."""
    import gigaam
    import torchaudio
    from silero_vad import get_speech_timestamps, load_silero_vad

    with tempfile.TemporaryDirectory() as tmp_dir:
        wav_path = Path(tmp_dir) / "audio.wav"
        _convert_to_wav(path, wav_path)

        waveform, sample_rate = torchaudio.load(str(wav_path))
        total_seconds = waveform.shape[1] / sample_rate

        vad_model = load_silero_vad()
        speech_timestamps = get_speech_timestamps(
            waveform[0], vad_model, sampling_rate=sample_rate, return_seconds=True,
        )
        boundaries = _segment_boundaries(speech_timestamps, total_seconds)

        asr_model = gigaam.load_model(MODEL_NAME, download_root=GIGAAM_DOWNLOAD_ROOT)
        lines = []
        for start, end in boundaries:
            segment = waveform[:, int(start * sample_rate):int(end * sample_rate)]
            if segment.shape[1] == 0:
                continue
            segment_path = Path(tmp_dir) / f"segment_{start:.2f}.wav"
            torchaudio.save(str(segment_path), segment, sample_rate)
            text = asr_model.transcribe(str(segment_path)).text.strip()
            if text:
                lines.append(f"[{start:.0f}s] {text}")
        return "\n".join(lines)
