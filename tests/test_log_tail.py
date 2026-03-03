from pathlib import Path

from utils.log_tail import read_file_tail_lines, read_file_tail_text


def test_read_file_tail_text_returns_full_for_small_file(tmp_path: Path) -> None:
    file_path = tmp_path / "small.log"
    file_path.write_text("line-1\nline-2\n", encoding="utf-8")

    assert read_file_tail_text(file_path, max_chars=100) == "line-1\nline-2\n"


def test_read_file_tail_text_truncates_large_file(tmp_path: Path) -> None:
    file_path = tmp_path / "large.log"
    content = "\n".join(f"line-{i:04d}" for i in range(500))
    file_path.write_text(content, encoding="utf-8")

    tail = read_file_tail_text(file_path, max_chars=60)
    assert tail.startswith("... (truncated)\n\n")
    assert "line-0499" in tail
    assert len(tail) > 60


def test_read_file_tail_lines_returns_last_lines(tmp_path: Path) -> None:
    file_path = tmp_path / "lines.log"
    file_path.write_text("\n".join(f"line-{i}" for i in range(20)), encoding="utf-8")

    assert read_file_tail_lines(file_path, max_lines=3) == "line-17\nline-18\nline-19"


def test_read_file_tail_lines_handles_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.log"
    assert read_file_tail_lines(missing, max_lines=10) == ""
