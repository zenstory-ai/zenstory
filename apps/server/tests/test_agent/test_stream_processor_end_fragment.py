"""Stream end inside a half-written </file>: the tag fragment is protocol, not body text."""

import pytest

from agent.core.stream_processor import StreamProcessor

BODY = "　　已生成的正文。"


def _finish(chunks):
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    streamed = ""
    for chunk in chunks:
        result = proc.process_content(chunk)
        streamed += result.file_content
    final = proc.finalize_on_stream_end()
    return streamed + final.file_content, final


@pytest.mark.parametrize("fragment", ["<", "</", "</fi", "</file", "< /fi", "</FILE"])
@pytest.mark.parametrize("split", [False, True])
def test_trailing_end_tag_prefix_is_not_saved(fragment, split):
    chunks = ["<file>" + BODY, fragment] if split else ["<file>" + BODY + fragment]

    streamed, final = _finish(chunks)

    assert final.file_complete is True
    assert final.auto_completed is True
    assert final.final_content == BODY
    assert final.content_length == len(BODY)
    assert streamed == BODY


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("　　他说 a < b。", "　　他说 a < b。"),
        ("　　段落</p", "　　段落</p"),
        ("　　代码：\n```\n</fi", "　　代码：\n```\n</fi"),
        ("　　正文<f", "　　正文<f"),
    ],
)
def test_other_trailing_text_is_kept(body, expected):
    _streamed, final = _finish(["<file>" + body])

    assert final.file_complete is True
    assert final.final_content == expected
