import json
from pathlib import Path

from pcs.research import youtube_subtitles
from pcs.research.youtube_subtitles import _safe_title, _upsert_index_entry


def test_safe_title_removes_filename_unsafe_characters():
    assert _safe_title('A: bad/title? "demo"') == "A badtitle demo"


def test_upsert_index_entry_replaces_matching_row(tmp_path):
    index_path = tmp_path / "transcripts_index.json"
    index_path.write_text(
        json.dumps(
            [
                {
                    "id": "abc",
                    "title": "Old",
                    "language": "en",
                    "source_url": "https://example.com/old",
                    "file": "abc - Old.en.srt",
                    "original_file": None,
                }
            ]
        ),
        encoding="utf-8",
    )

    _upsert_index_entry(
        index_path,
        {
            "id": "abc",
            "title": "New",
            "language": "en",
            "source_url": "https://example.com/new",
            "file": "abc - Old.en.srt",
            "original_file": None,
        },
    )

    rows = json.loads(index_path.read_text(encoding="utf-8"))
    assert rows == [
        {
            "id": "abc",
            "title": "New",
            "language": "en",
            "source_url": "https://example.com/new",
            "file": "abc - Old.en.srt",
            "original_file": None,
        }
    ]


def test_download_uses_title_only_for_filename(tmp_path, monkeypatch):
    def fake_video_info(url):
        return {
            "id": "abc123",
            "title": "Demo Video",
            "webpage_url": url,
        }

    def fake_run(cmd, check):
        Path(tmp_path / "Demo Video.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")

    monkeypatch.setattr(youtube_subtitles, "_video_info", fake_video_info)
    monkeypatch.setattr(youtube_subtitles.subprocess, "run", fake_run)

    files = youtube_subtitles.download_youtube_subtitles(
        "https://www.youtube.com/watch?v=abc123",
        output_dir=tmp_path,
    )

    assert [path.name for path in files] == ["Demo Video.en.srt"]
    rows = json.loads((tmp_path / "transcripts_index.json").read_text(encoding="utf-8"))
    assert rows[0]["file"] == "Demo Video.en.srt"
