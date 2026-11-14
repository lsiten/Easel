"""Local-only speech evidence for video drafts; never silently drop an audio track."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import anyio
from pydantic import BaseModel, ConfigDict, Field

from easel.generation_images import ImageGeneration
from easel.generation_models import GenerationError
from easel.ownership import owned_media_path


class AudioStream(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    index: int


class AudioProbe(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    streams: tuple[AudioStream, ...] = ()


class SpeechSegment(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    start: float
    end: float
    text: str = Field(max_length=10000)


class SpeechTranscript(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    segments: tuple[SpeechSegment, ...] = ()
    language: str | None = None
    source_sha256: str = ''


class LocalDraftSpeech:
    def __init__(self, outputs: Path, settings: Mapping[str, str]) -> None:
        self.outputs, self.settings = outputs, settings

    def model_path(self) -> Path:
        """Resolve existing local weights only; a model identifier is never sent to a downloader."""
        if importlib.util.find_spec('faster_whisper') is None:
            raise GenerationError('draft_audio_transcription_dependency_required', 409)
        requested = self.settings.get('EASEL_DRAFT_ASR_MODEL', '').strip()
        names = (requested,) if requested else ('base', 'tiny', 'large-v3')
        candidates: list[Path] = []
        for name in names:
            candidate = Path(name).expanduser()
            if candidate.is_absolute():
                candidates.append(candidate)
            elif name in ('tiny', 'base', 'small', 'medium', 'large-v3'):
                candidates.append(Path.home()/'.cache/easel-models'/('faster-whisper-'+name))
                cache = Path(os.environ.get('HF_HUB_CACHE', str(Path.home()/'.cache/huggingface/hub')))
                candidates.extend(sorted((cache/('models--Systran--faster-whisper-'+name)/'snapshots').glob('*')))
                if name == 'large-v3':
                    candidates.append(Path.home()/'models'/'whisper-large-v3')
        for candidate in candidates:
            if all((candidate/name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json')):
                return candidate.resolve()
        raise GenerationError('draft_audio_transcription_model_required', 409)

    async def transcribe(self, audio: Path, destination: Path) -> SpeechTranscript:
        """Reuse cached Whisper inference and asr.py formatting with hard offline mode."""
        model = self.model_path()
        code = 'from pathlib import Path; import sys; from easel.generation_draft_speech import transcribe_local; transcribe_local(*(Path(value) for value in sys.argv[1:]))'
        with anyio.fail_after(600):
            result = await anyio.run_process([sys.executable, '-c', code, str(audio), str(model), str(destination)],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode or not destination.is_file():
            raise GenerationError('draft_audio_transcription_failed', 502)
        return SpeechTranscript.model_validate_json(destination.read_bytes())

    async def analyze(self, persona: str, relative: str) -> SpeechTranscript | None:
        _, video = owned_media_path(self.outputs, persona, relative)
        with anyio.fail_after(20):
            result = await anyio.run_process(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'json', str(video)], check=False)
        if result.returncode:
            raise GenerationError('draft_audio_probe_failed', 502)
        if not AudioProbe.model_validate_json(result.stdout).streams:
            return None
        digest = await anyio.to_thread.run_sync(ImageGeneration._hash, video)
        destination = video.with_name(video.stem+'-draft-transcript.json')
        audio = video.with_name(video.stem+'-draft-audio.wav')
        if destination.is_symlink() or audio.is_symlink():
            raise GenerationError('draft_audio_path_unsafe', 409)
        if destination.is_file():
            cached = SpeechTranscript.model_validate_json(destination.read_bytes())
            if cached.source_sha256 == digest:
                return cached
        with anyio.fail_after(90):
            extracted = await anyio.run_process(['ffmpeg', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', str(audio)], check=False)
        if extracted.returncode:
            raise GenerationError('draft_audio_extract_failed', 502)
        try:
            transcript = await self.transcribe(audio, destination)
        finally:
            audio.unlink(missing_ok=True)
        if sum(len(segment.text) for segment in transcript.segments) > 60000:
            raise GenerationError('draft_audio_transcript_too_large', 409)
        saved = transcript.model_copy(update={'source_sha256': digest})
        destination.write_text(saved.model_dump_json(), encoding='utf-8')
        return saved


def transcribe_local(audio: Path, model_path: Path, destination: Path) -> None:
    """Reuse ASR formatting, feeding PCM directly to avoid incompatible PyAV file decoders."""
    import wave

    import numpy as np
    from faster_whisper import WhisperModel

    from skills.shared.scripts.asr import _render_json, _split_long_segment
    with wave.open(str(audio)) as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16000 or stream.getsampwidth() != 2:
            raise GenerationError('draft_audio_pcm_invalid')
        samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', local_files_only=True)
    segments, info = model.transcribe(samples, vad_filter=True, beam_size=5)
    cues = [cue for segment in segments for cue in _split_long_segment(segment.text.strip(), segment.start, segment.end, 100)]
    destination.write_text(_render_json(cues, {'language': info.language}), encoding='utf-8')
