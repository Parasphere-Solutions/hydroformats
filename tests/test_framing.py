"""Framing: line reading tolerance and quoted-field tokenizing."""
import io

from hydroformats.framing import iter_lines, tokenize


def test_lines_frame_tag_and_body():
    lines = list(iter_lines(io.StringIO("POS 0 1.0 2.0 3.0\nEOH\n")))
    assert [(ln.tag, ln.body) for ln in lines] == [("POS", "0 1.0 2.0 3.0"), ("EOH", "")]
    assert lines[0].number == 1


def test_crlf_blank_lines_and_whitespace_are_tolerated():
    text = "POS 0 1 2 3\r\n\r\n   \nGYR 0 1 90\r\n"
    lines = list(iter_lines(io.StringIO(text)))
    assert [ln.tag for ln in lines] == ["POS", "GYR"]
    assert lines[1].number == 4  # numbering counts physical lines


def test_short_and_tagless_lines_never_raise():
    lines = list(iter_lines(io.StringIO("X\n12 34\n")))
    assert [(ln.tag, ln.body) for ln in lines] == [("X", ""), ("12", "34")]


def test_tokenize_quoted_fields():
    fields = tokenize('"Jane Q. Surveyor" "Boat 5" "" plain 1.5')
    assert fields == ("Jane Q. Surveyor", "Boat 5", "", "plain", "1.5")


def test_tokenize_unterminated_quote_consumes_rest():
    assert tokenize('0 276 "Simulation dll v9') == ("0", "276", "Simulation dll v9")


def test_tokenize_empty_body():
    assert tokenize("") == ()
