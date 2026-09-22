import pytest

from easy_agent.knowledge.api import (
    _download_content_type,
    _parse_single_range,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.PDF", "application/pdf"),
        (
            "market.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "minutes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
def test_download_content_type_uses_filename_extension(filename, expected):
    assert _download_content_type(filename, "application/octet-stream") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("bytes=0-5", (0, 5)),
        ("bytes=6-", (6, 9)),
        ("bytes=-4", (6, 9)),
        ("bytes=-20", (0, 9)),
    ],
)
def test_parse_single_range_supports_standard_single_ranges(value, expected):
    assert _parse_single_range(value, 10) == expected


@pytest.mark.parametrize(
    "value",
    ["bytes=", "bytes=10-11", "bytes=6-5", "bytes=0-1,3-4", "items=0-1"],
)
def test_parse_single_range_rejects_invalid_or_multiple_ranges(value):
    with pytest.raises(ValueError):
        _parse_single_range(value, 10)
