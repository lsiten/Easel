#!/usr/bin/env python3
"""ref_analysis.py — 参考视频分析（Video Reference Analyst）的确定性封装。

给 video-reference-analyst SKILL 用：把用户给的"参考视频"（本地路径或 URL）
拆成可分析的结构，交给 Agent 的视觉模型去"看懂"并产出 5-aspect 分镜简报。
本脚本只负责"取数据"（下载/分镜/抽帧/时长/音轨/转写），不做视觉理解——
视觉理解、5-aspect 归类、motion_type 判定由 Agent 完成。

思路参考 OpenMontage tools/analysis/{frame_sampler,scene_detect,video_analyzer}.py，
按 Easel 共享脚本风格落地：纯标准库 + ffmpeg/ffprobe，可选 PySceneDetect / yt-dlp，
转写复用 asr.py。无 API key。

子命令（均支持 -h）：
    analyze   分析参考视频 → video_analysis_brief.json + keyframes/
    selftest  自检（ffmpeg/ffprobe 是否可用）

用法举例：
    ref_analysis.py analyze --source https://www.youtube.com/watch?v=XXXX \
        --depth standard --max-keyframes 16 -o outputs/ref/ref1
    ref_analysis.py analyze --source /path/to/raw.mp4 --depth deep -o outputs/ref/r2
    ref_analysis.py analyze --source ref.mp4 --transcript-only
    ref_analysis.py selftest

analysis_depth：
    transcript_only  只转写 + 元数据（最快）
    standard         + 分镜 + 抽帧 + 音轨能量
    deep             + 每镜头内多帧采样（更细，用于逐镜 motion 判定）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_URL_RE = re.compile(r"^(https?|ftp)://", re.IGNORECASE)
_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts", ".wmv"}


def _die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _check_ffmpeg() -> None:
    for t in ("ffmpeg", "ffprobe"):
        if shutil.which(t) is None:
            _die(f"未找到 {t}，请先安装 ffmpeg（含 ffprobe）。", 3)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kw)


def _probe(path: Path) -> dict:
    """ffprobe 取元数据：时长/宽高/帧率/音轨。"""
    out = _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout or "{}")
    info: dict = {"path": str(path), "duration": 0.0, "width": 0, "height": 0,
                  "fps": 0.0, "has_audio": False}
    fmt = data.get("format", {})
    info["duration"] = float(fmt.get("duration") or 0.0)
    info["size_bytes"] = int(fmt.get("size") or 0)
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            info["width"] = int(s.get("width") or 0)
            info["height"] = int(s.get("height") or 0)
            raw = s.get("r_frame_rate", "0/1")
            num, den = (raw.split("/") if "/" in raw else (raw, "1"))
            try:
                info["fps"] = float(num) / float(den) if float(den) else 0.0
            except ValueError:
                info["fps"] = 0.0
            break
        if s.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


def _download(source: str, out_dir: Path) -> Path:
    """URL 源：yt-dlp 可用则下载，否则报错提示。"""
    if shutil.which("yt-dlp") is None and shutil.which("yt_dlp") is None:
        _die("需要下载 URL 但未找到 yt-dlp；请 `pip install yt-dlp` 或先把视频存为本地文件。", 4)
    cmd = [shutil.which("yt-dlp") or "yt-dlp", "-f", "mp4[height<=720]/*[height<=720]/*",
           "-o", str(out_dir / "download.%(ext)s"), "--no-playlist", source]
    _run(cmd, check=True)
    for p in out_dir.iterdir():
        if p.suffix.lower() in _VIDEO_EXT:
            return p
    _die("yt-dlp 下载后未找到视频文件。", 4)


def _detect_scenes(path: Path, depth: str) -> list[dict]:
    """分镜：优先 PySceneDetect，缺失则按固定间隔切。返回 [{start,end,duration}]。"""
    try:
        from scenedetect import detect, ContentDetector  # type: ignore
        scenes = detect(str(path), ContentDetector())
        result = []
        for i, (start, end) in enumerate(scenes):
            s = start.get("timecode", start) if isinstance(start, dict) else start
            e = end.get("timecode", end) if isinstance(end, dict) else end
            result.append({"id": i, "start": float(s), "end": float(e),
                           "duration": float(e) - float(s)})
        if result:
            return result
    except Exception:
        pass
    # 退化为间隔切分
    dur = _probe(path)["duration"] or 0.0
    interval = 6.0 if depth == "deep" else 4.0
    result = []
    t = 0.0
    i = 0
    while t < dur:
        result.append({"id": i, "start": t, "end": min(t + interval, dur),
                       "duration": min(interval, dur - t)})
        t += interval
        i += 1
    return result


def _extract_keyframes(path: Path, scenes: list[dict], max_frames: int,
                       depth: str, out_dir: Path) -> list[dict]:
    """按场景抽关键帧；deep 时每镜头抽 2 帧，standard/transcript 每镜头 1 帧。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Avoid presenting frames from an older, longer analysis as current evidence.
    for stale in out_dir.glob("kf_*.jpg"):
        stale.unlink(missing_ok=True)
    per_scene = 2 if depth == "deep" else 1
    frames: list[dict] = []
    for sc in scenes:
        for k in range(per_scene):
            frac = 0.5 if per_scene == 1 else (0.3 if k == 0 else 0.7)
            t = sc["start"] + frac * max(sc["duration"], 0.0)
            t = min(t, max(sc["end"] - 0.05, sc["start"]))
            fp = out_dir / f"kf_{sc['id']:03d}_{k}.jpg"
            if fp.exists() or True:
                ok = _run(
                    ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(path),
                     "-frames:v", "1", "-q:v", "3", "-vf", "scale=640:-1", str(fp)],
                    capture_output=True,
                )
                if ok.returncode == 0 and fp.exists() and fp.stat().st_size > 0:
                    frames.append({"scene_id": sc["id"], "timestamp": round(t, 2),
                                   "path": str(fp), "frame": k})
            if len(frames) >= max_frames:
                return frames
    return frames


def _audio_energy(path: Path) -> dict:
    """粗略音轨能量：抽 10 秒均值（用于判断 BGM/口播强度）。"""
    if not _probe(path).get("has_audio"):
        return {"has_audio": False}
    try:
        out = _run(
            ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        m = re.search(r"mean_volume:\s*(-?\d+\.\d+)\s*dB", out.stderr or "")
        return {"has_audio": True, "mean_volume_db": float(m.group(1)) if m else None}
    except Exception:
        return {"has_audio": True, "mean_volume_db": None}


def _transcribe(path: Path, out_dir: Path, language: str) -> dict:
    """复用 asr.py 生成转写（失败不阻断）。"""
    here = Path(__file__).resolve().parent
    asr = here / "asr.py"
    if not asr.exists():
        return {}
    srt = out_dir / "transcript.srt"
    srt.unlink(missing_ok=True)
    ok = _run([sys.executable, str(asr), "transcribe", "-i", str(path),
              "-o", str(srt), "--format", "srt", "--model", "small",
              "--language", language or "auto"], capture_output=True)
    if ok.returncode != 0 or not srt.exists():
        return {}
    text = srt.read_text(encoding="utf-8", errors="ignore")
    segments = _parse_srt(text)
    return {"transcript": " ".join(item["text"] for item in segments),
            "transcript_file": str(srt), "segments": segments,
            "status": "succeeded" if segments else "partial"}


def _parse_srt(text: str) -> list[dict[str, Any]]:
    """Parse timed SRT cues without trusting cue text as instructions."""
    result: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", text.strip()) if text.strip() else []
    timestamp = re.compile(
        r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
    )

    def seconds(value: str) -> float:
        h, m, rest = value.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        match = next((timestamp.search(line) for line in lines if timestamp.search(line)), None)
        if not match:
            continue
        text_lines = lines[lines.index(next(line for line in lines if timestamp.search(line))) + 1:]
        cue_text = " ".join(text_lines).strip()
        if not cue_text:
            continue
        result.append({"index": len(result), "start": round(seconds(match.group("start")), 3),
                       "end": round(seconds(match.group("end")), 3), "text": cue_text,
                       "confidence": None, "confidence_state": "unknown"})
    return result


def _relative_output_path(path: str | Path, out_dir: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(out_dir.resolve()).as_posix()
    except ValueError:
        return None


def _tokenize_transcript(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Return word anchors, using timing only when ASR supplied word timestamps.

    Segment/SRT cues provide timing for the whole segment.  Their duration cannot
    be assigned to individual words without fabricating precision, so token timing
    remains unknown unless a word-level ``words`` array contains valid timestamps.
    """
    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    if not isinstance(segments, list):
        segments = []
    tokens: list[dict[str, Any]] = []
    word_re = re.compile(r"\S+", re.UNICODE)
    for segment_index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        words = word_re.findall(str(segment.get("text") or ""))
        supplied_words = segment.get("words")
        if not isinstance(supplied_words, list):
            supplied_words = []
        confidence = segment.get("confidence")
        confidence_known = isinstance(confidence, (int, float)) and 0 <= confidence <= 1
        for word_index, word in enumerate(words):
            token: dict[str, Any] = {
                "index": len(tokens), "segment_index": segment_index, "text": word,
                "start": None, "end": None, "timing_state": "unknown",
                "confidence": float(confidence) if confidence_known else None,
                "confidence_state": "known" if confidence_known else "unknown",
            }
            word_info = supplied_words[word_index] if word_index < len(supplied_words) else None
            if isinstance(word_info, dict):
                start = word_info.get("start")
                end = word_info.get("end")
                timed = (isinstance(start, (int, float)) and
                          isinstance(end, (int, float)) and end >= start)
                if timed:
                    token["start"] = round(float(start), 3)
                    token["end"] = round(float(end), 3)
                    token["timing_state"] = "known"
                word_confidence = word_info.get("confidence")
                if isinstance(word_confidence, (int, float)) and 0 <= word_confidence <= 1:
                    token["confidence"] = float(word_confidence)
                    token["confidence_state"] = "known"
            tokens.append(token)
    return tokens


def _align_scene_transcript(scene: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any]:
    matched = []
    for index, segment in enumerate(segments):
        start, end = segment.get("start"), segment.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            if float(start) < float(scene["end"]) and float(end) > float(scene["start"]):
                matched.append(index)
    return {"segment_indices": matched,
            "state": "aligned" if matched else ("unknown" if not segments else "none")}


def _semanticize(brief: dict[str, Any], out_dir: Path) -> None:
    """Persist semantic analysis fields while keeping the legacy brief shape."""
    transcript = brief.get("transcript") if isinstance(brief.get("transcript"), dict) else {}
    segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    tokens = _tokenize_transcript(transcript)
    brief["whole_piece"] = {
        "duration": brief.get("metadata", {}).get("duration"),
        "resolution": {"width": brief.get("metadata", {}).get("width"),
                        "height": brief.get("metadata", {}).get("height")},
        "fps": brief.get("metadata", {}).get("fps"),
        "has_audio": bool(brief.get("has_audio")),
        "status": "succeeded" if brief.get("metadata") else "partial",
    }
    brief["tokens"] = tokens
    brief["word_timing"] = tokens
    brief["token_anchors"] = tokens
    brief["transcript_alignment"] = {
        "status": "aligned" if segments and tokens else ("partial" if transcript else "unknown"),
        "segment_count": len(segments), "token_count": len(tokens),
        "timing_state": "known" if any(item["timing_state"] == "known" for item in tokens) else "unknown",
    }
    scenes = brief.get("scenes") if isinstance(brief.get("scenes"), list) else []
    for index, scene in enumerate(scenes):
        scene["id"] = str(scene.get("id", index))
        scene["relationships"] = {
            "previous_scene_id": str(scenes[index - 1].get("id")) if index else None,
            "next_scene_id": str(scenes[index + 1].get("id")) if index + 1 < len(scenes) else None,
        }
        scene["transcript_alignment"] = _align_scene_transcript(scene, segments)
        scene["five_aspect"] = {
            "subject": {"value": None, "state": "unknown"},
            "subject_motion": {"value": None, "state": "unknown"},
            "scene": {"value": None, "state": "unknown"},
            "spatial_framing": {"value": None, "state": "unknown"},
            "camera": {"value": None, "state": "unknown"},
        }
        scene["motion"] = {"type": "unknown", "state": "unknown", "confidence": None}
        scene["evidence_paths"] = [
            item["path"] for item in brief.get("keyframes", [])
            if item.get("scene_id") == index and item.get("path")
            and _relative_output_path(item["path"], out_dir)
        ]
        scene["evidence_paths"] = [_relative_output_path(item, out_dir) or item
                                    for item in scene["evidence_paths"]]
    source_path = brief.get("file")
    evidence = {"brief": "video_analysis_brief.json"}
    transcript_file = transcript.get("transcript_file")
    if transcript_file:
        evidence["transcript"] = _relative_output_path(transcript_file, out_dir) or "transcript.srt"
    evidence["keyframes"] = "keyframes/"
    brief["evidence"] = {"paths": evidence, "source": "video_analysis_brief.json",
                         "source_path_state": "external" if source_path else "unknown"}
    brief["source_evidence_paths"] = list(evidence.values())
    brief["analysis_status"] = "succeeded" if scenes else "partial"


def cmd_analyze(a) -> int:
    _check_ffmpeg()
    out_dir = Path(a.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()

    # 1) 源
    if _URL_RE.match(a.source):
        src_path = _download(a.source, out_dir)
    else:
        src_path = Path(a.source).expanduser()
        if not src_path.is_file():
            _die(f"输入文件不存在: {src_path}", 2)

    meta = _probe(src_path)
    brief: dict = {
        "source": a.source,
        "file": str(src_path),
        "analysis_depth": a.depth,
        "metadata": meta,
        "has_audio": meta.get("has_audio", False),
        "scenes": [],
        "keyframes": [],
        "audio": {},
        "transcript": {},
        "motion_summary": {},
    }
    print(f"[ref_analysis] 元数据: {meta['duration']:.1f}s {meta['width']}x{meta['height']} "
          f"{meta['fps']:.2f}fps audio={meta.get('has_audio')}")

    if a.depth == "transcript_only":
        brief["transcript"] = _transcribe(src_path, out_dir, a.language)
        brief["audio"] = _audio_energy(src_path)
    else:
        brief["audio"] = _audio_energy(src_path)
        brief["scenes"] = _detect_scenes(src_path, a.depth)
        print(f"[ref_analysis] 分镜 {len(brief['scenes'])} 段")
        kf_dir = out_dir / "keyframes"
        brief["keyframes"] = _extract_keyframes(src_path, brief["scenes"],
                                               a.max_keyframes, a.depth, kf_dir)
        print(f"[ref_analysis] 关键帧 {len(brief['keyframes'])} 张 → {kf_dir}")
        brief["transcript"] = _transcribe(src_path, out_dir, a.language)

    if not brief["transcript"]:
        brief["transcript"] = {"status": "unknown", "reason": "ASR unavailable or failed",
                                "transcript": "", "segments": []}

    # 占位：Agent 读 keyframes 后回填 5-aspect + motion_type
    brief["motion_summary"] = {
        "motion_type": "unknown",
        "state": "unknown",
        "confidence": None,
        "note": "由 Agent 读 keyframes 判定：motion_clip / animated_still / static_image / mixed",
    }
    brief["five_aspect"] = {
        "subject": [], "subject_motion": [], "scene": [],
        "spatial_framing": [], "camera": [],
        "note": "由 Agent 逐镜/逐镜组填写（CMU/Harvard CHAI 5-aspect 结构）",
    }

    _semanticize(brief, out_dir)

    brief_path = out_dir / "video_analysis_brief.json"
    tmp_brief = brief_path.with_suffix(".json.tmp")
    try:
        tmp_brief.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_brief.replace(brief_path)
    finally:
        tmp_brief.unlink(missing_ok=True)
    elapsed = time.time() - start
    print(f"[ref_analysis] 完成 ({elapsed:.1f}s) → {brief_path}")
    print(f"[ref_analysis] 关键帧目录: {out_dir / 'keyframes'}")
    return 0


def cmd_selftest(a) -> int:
    ok = True
    for t in ("ffmpeg", "ffprobe"):
        found = shutil.which(t) is not None
        print(f"  {t}: {'OK' if found else 'MISSING'}")
        ok = ok and found
    try:
        import scenedetect  # type: ignore
        print("  scenedetect: OK（可选，缺失则按间隔分镜）")
    except ImportError:
        print("  scenedetect: MISSING（可选，缺失则按间隔分镜）")
    try:
        import yt_dlp  # type: ignore
        print("  yt-dlp: OK（可选，URL 源需此依赖）")
    except ImportError:
        print("  yt-dlp: MISSING（可选，URL 源需此依赖）")
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="参考视频分析（Video Reference Analyst）")
    ap.add_argument("--selftest", action="store_true", help="自检 ffmpeg/可选依赖")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("analyze", help="分析参考视频 → brief + keyframes")
    p.add_argument("--source", required=True, help="本地视频路径或 URL")
    p.add_argument("--depth", choices=["transcript_only", "standard", "deep"],
                  default="standard")
    p.add_argument("--transcript-only", dest="depth", action="store_const",
                  default="transcript_only", const="transcript_only")
    p.add_argument("--max-keyframes", type=int, default=16)
    p.add_argument("--language", default="auto")
    p.add_argument("-o", "--out", default="outputs/ref",
                  help="输出目录（默认 outputs/ref）")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("selftest", help="自检")
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    if getattr(args, "selftest", False) or not getattr(args, "cmd", None):
        return cmd_selftest(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
