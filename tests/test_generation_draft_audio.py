"""Spoken source meaning reaches captions; unavailable ASR never degrades silently."""
from pathlib import Path
import json
import shutil
import subprocess
import wave
import anyio
import httpx
from fastapi import FastAPI
from easel.generation_draft import DraftGeneration
from easel.generation_draft_runtime import NativeDraftRuntime
from easel.generation_draft_router import create_draft_router
from easel.generation_draft_speech import LocalDraftSpeech, SpeechTranscript, SpeechSegment
from tests.test_generation_draft_native import provider


def audio_video(tmp_path: Path) -> Path:
    fixture = tmp_path/'spoken.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=96x96:d=1',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=1',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(fixture)], check=True, capture_output=True)
    return fixture


def test_audio_track_transcript_changes_persisted_caption(tmp_path: Path, monkeypatch) -> None:
    fixture = audio_video(tmp_path)
    def download(url, destination, **kwargs):
        shutil.copyfile(fixture, destination)
    monkeypatch.setattr('easel.content_library_import.download_to_file', download)
    speech_calls = []
    async def transcribe(self, audio, destination):
        with wave.open(str(audio)) as extracted:
            assert extracted.getframerate() == 16000 and extracted.getnchannels() == 1
            assert extracted.getnframes() > 0
        speech_calls.append(audio)
        return SpeechTranscript(language='en', segments=(SpeechSegment(start=0, end=1,
            text='Sunday workshop tickets cost twenty dollars. Do not describe this event as free.'),))
    monkeypatch.setattr(LocalDraftSpeech, 'transcribe', transcribe)
    with provider() as (settings, calls):
        async def scenario() -> None:
            service = DraftGeneration(tmp_path, NativeDraftRuntime(tmp_path, Path.cwd(), settings))
            app = FastAPI()
            app.include_router(create_draft_router(lambda: service, lambda request, persona: 'alice'))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/ai/draft-generation/from-video-url',
                    json={'videoUrl': 'https://93.184.216.34/spoken.mp4'}, headers={'Idempotency-Key': 'spoken'})
                assert response.status_code == 200, response.text
                job = service.store.list('alice').items[0]
                assert job.response.description == 'Sunday workshop. Tickets: $20.'
                evidence = next(part['text'] for part in calls[0]['messages'][1]['content'] if part.get('text', '').startswith('VIDEO_AUDIO_EVIDENCE'))
                assert 'Do not describe this event as free.' in evidence
                assert len(speech_calls) == 1
                repeated = await client.post('/api/ai/draft-generation/from-video-url',
                    json={'videoUrl': 'https://93.184.216.34/spoken.mp4'}, headers={'Idempotency-Key': 'spoken'})
                assert repeated.json() == response.json() and len(speech_calls) == 1
        anyio.run(scenario)


def test_audio_without_available_model_fails_before_caption_call(tmp_path: Path, monkeypatch) -> None:
    fixture = audio_video(tmp_path)
    monkeypatch.setattr('easel.content_library_import.download_to_file', lambda url, destination, **kwargs: shutil.copyfile(fixture, destination))
    with provider() as (settings, calls):
        settings['EASEL_DRAFT_ASR_MODEL'] = str(tmp_path/'missing-local-weights')
        async def scenario() -> None:
            service = DraftGeneration(tmp_path, NativeDraftRuntime(tmp_path, Path.cwd(), settings))
            app = FastAPI()
            app.include_router(create_draft_router(lambda: service, lambda request, persona: 'alice'))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/ai/draft-generation/from-video-url', json={'videoUrl': 'https://93.184.216.34/spoken.mp4'})
                assert response.status_code == 409
                assert response.json()['detail'] == 'draft_audio_transcription_model_required'
                assert calls == []
        anyio.run(scenario)
