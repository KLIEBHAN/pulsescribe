from pathlib import Path

from utils.log_tail import (
    clamp_scroll_value,
    get_file_signature,
    is_near_bottom,
    read_file_tail_lines,
    read_file_tail_text,
    should_auto_refresh_logs,
)


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


def test_get_file_signature_returns_none_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.log"
    assert get_file_signature(missing) is None


def test_get_file_signature_changes_when_file_content_changes(tmp_path: Path) -> None:
    file_path = tmp_path / "signature.log"
    file_path.write_text("abc", encoding="utf-8")
    signature_before = get_file_signature(file_path)

    file_path.write_text("abcdef", encoding="utf-8")
    signature_after = get_file_signature(file_path)

    assert signature_before is not None
    assert signature_after is not None
    assert signature_before != signature_after


def test_is_near_bottom_with_tolerance() -> None:
    assert is_near_bottom(90, 100) is True
    assert is_near_bottom(89, 100) is False
    assert is_near_bottom(0, 0) is True


def test_clamp_scroll_value() -> None:
    assert clamp_scroll_value(50, 120) == 50
    assert clamp_scroll_value(-5, 120) == 0
    assert clamp_scroll_value(999, 120) == 120


def test_should_auto_refresh_logs_only_when_logs_visible() -> None:
    assert (
        should_auto_refresh_logs(
            enabled=True,
            is_logs_tab_active=True,
            logs_view_index=0,
        )
        is True
    )
    assert (
        should_auto_refresh_logs(
            enabled=False,
            is_logs_tab_active=True,
            logs_view_index=0,
        )
        is False
    )
    assert (
        should_auto_refresh_logs(
            enabled=True,
            is_logs_tab_active=False,
            logs_view_index=0,
        )
        is False
    )
    assert (
        should_auto_refresh_logs(
            enabled=True,
            is_logs_tab_active=True,
            logs_view_index=1,
        )
        is False
    )
