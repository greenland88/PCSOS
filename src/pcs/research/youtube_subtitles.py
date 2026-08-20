import json
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_TRANSCRIPT_DIR = Path("research/creditspreadinvesting/transcripts")


def _safe_title(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip()
    safe = re.sub(r"\s+", " ", safe)
    return safe[:160] or "untitled"


def _load_index(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_index(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _upsert_index_entry(index_path: Path, entry: dict) -> None:
    rows = _load_index(index_path)
    key = (entry["id"], entry["language"], entry["file"])
    rows = [
        row
        for row in rows
        if (row.get("id"), row.get("language"), row.get("file")) != key
    ]
    rows.append(entry)
    rows.sort(key=lambda row: (row.get("id", ""), row.get("language", ""), row.get("file", "")))
    _write_index(index_path, rows)


def _video_info(url: str) -> dict:
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--skip-download",
        "--dump-json",
        "--no-warnings",
        url,
    ]
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def download_youtube_subtitles(
    url: str,
    output_dir: Path = DEFAULT_TRANSCRIPT_DIR,
    languages: str = "en,en-orig",
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    info = _video_info(url)
    video_id = info["id"]
    title = _safe_title(info.get("title") or video_id)
    output_template = str(output_dir / f"{title}.%(ext)s")

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        languages,
        "--sub-format",
        "srt",
        "--convert-subs",
        "srt",
        "--output",
        output_template,
        url,
    ]
    subprocess.run(cmd, check=True)

    files = sorted(output_dir.glob(f"{title}.*.srt"))
    index_path = output_dir / "transcripts_index.json"
    for file_path in files:
        suffix = file_path.name.removeprefix(f"{title}.").removesuffix(".srt")
        _upsert_index_entry(
            index_path,
            {
                "id": video_id,
                "title": title,
                "language": suffix,
                "source_url": info.get("webpage_url") or url,
                "file": file_path.name,
                "original_file": None,
            },
        )
    return files
