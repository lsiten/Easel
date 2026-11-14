"""Best-effort local evidence extraction for asset organization."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

_DATE_IN_PATH = re.compile(r"(?P<year>20\d{2})[-_]?(?P<month>0[1-9]|1[0-2])[-_]?(?P<day>0[1-9]|[12]\d|3[01])")
_IGNORED_LOCATION = {"assets", "asset", "images", "image", "videos", "video", "temp", "tmp",
                     "drafts", "draft", "raw", "downloads", "图片", "视频", "素材"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_capture_time(path: Path) -> str:
    for part in reversed(path.parts[:-1]):
        match = _DATE_IN_PATH.search(part)
        if match:
            try:
                return datetime(int(match.group("year")), int(match.group("month")),
                                int(match.group("day"))).isoformat(timespec="seconds")
            except ValueError:
                continue
    return ""


def _image_exif(path: Path) -> tuple[str, str]:
    try:
        from PIL import ExifTags, Image
        with Image.open(path) as image:
            exif = image.getexif()
    except (ImportError, OSError, ValueError):
        return "", ""
    captured = str(exif.get(36867) or "").strip()
    if captured:
        try:
            captured = datetime.strptime(captured, "%Y:%m:%d %H:%M:%S").isoformat(timespec="seconds")
        except ValueError:
            captured = ""
    location = ""
    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        latitude = _gps_decimal(gps.get(2), gps.get(1))
        longitude = _gps_decimal(gps.get(4), gps.get(3))
        if latitude is not None and longitude is not None:
            location = f"GPS:{latitude:.6f},{longitude:.6f}"
    except (AttributeError, TypeError, ValueError):
        pass
    return captured, location


def _gps_decimal(value: object, reference: object) -> float | None:
    if not isinstance(value, tuple) or len(value) != 3:
        return None
    degrees, minutes, seconds = (float(part) for part in value)
    result = degrees + minutes / 60 + seconds / 3600
    if str(reference) in {"S", "W"}:
        result = -result
    return result


def _video_creation_time(path: Path) -> str:
    executable = shutil.which("ffprobe")
    if executable is None:
        return ""
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-show_entries", "format_tags=creation_time",
             "-of", "json", str(path.resolve())],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False,
        )
        value = json.loads(result.stdout or b"{}")
        tags = value.get("format", {}).get("tags", {}) if isinstance(value, dict) else {}
        captured = tags.get("creation_time", "") if isinstance(tags, dict) else ""
        return str(captured).replace("Z", "+00:00")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def _location_hint(path: Path) -> str:
    for part in reversed(path.parts[:-1]):
        clean = part.strip()
        if clean and clean.lower() not in _IGNORED_LOCATION and not _DATE_IN_PATH.search(clean):
            return clean[:120]
    return ""


def extract_evidence(path: Path) -> dict[str, str]:
    """Return content fingerprint and evidence-backed time/location hints."""
    captured_at, location, time_source = "", "", ""
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}:
        captured_at, location = _image_exif(path)
        if captured_at:
            time_source = "exif"
    elif path.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}:
        captured_at = _video_creation_time(path)
        if captured_at:
            time_source = "video_metadata"
    if not captured_at:
        captured_at = _path_capture_time(path)
        if captured_at:
            time_source = "path"
    if not captured_at:
        stat = path.stat()
        timestamp = getattr(stat, "st_birthtime", stat.st_mtime)
        captured_at = datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
        time_source = "filesystem"
    return {
        "sha256": sha256_file(path),
        "capturedAt": captured_at,
        "locationHint": location or _location_hint(path),
        "timeSource": time_source,
    }
