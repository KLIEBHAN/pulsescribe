import pytest

from utils.text_preview import normalized_text_prefix


@pytest.mark.parametrize("budget", [0, 1, 2, 8, 20, 45, 100])
@pytest.mark.parametrize(
    "text",
    [
        "",
        " \n\t ",
        "alpha beta",
        "  alpha\n\t beta   ",
        "ä\u2003β\u00a0語\r\nend",
        "x" * 200,
    ],
)
def test_normalized_prefix_matches_full_normalization(text, budget):
    assert (
        normalized_text_prefix(text, max_chars=budget)
        == " ".join(text.split())[:budget]
    )


def test_normalized_prefix_stops_scanning_when_preview_is_complete():
    class CountingText(str):
        scanned = 0

        def __iter__(self):
            for char in super().__iter__():
                self.scanned += 1
                yield char

    text = CountingText("alpha beta " * 10_000)

    assert normalized_text_prefix(text, max_chars=20) == "alpha beta alpha bet"
    assert text.scanned == 20
