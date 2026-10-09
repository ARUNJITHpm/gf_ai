from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import Sender
from app.services.export_parser import (
    DateOrder,
    ExportParseError,
    detect_date_order,
    map_authors,
    parse_export,
)

FIXTURES = Path(__file__).parent / "fixtures" / "exports"
IST = ZoneInfo("Asia/Kolkata")


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_dmy_24h_export() -> None:
    parsed = parse_export(read("android_dmy_24h.txt"), IST)
    assert parsed.date_order == DateOrder.DMY
    assert parsed.system_lines == 2
    assert parsed.authors == ["Alex", "Sam"]
    assert len(parsed.messages) == 9

    first = parsed.messages[0]
    assert (first.author, first.text) == ("Sam", "Good morning!")
    # 09:16 IST == 03:46 UTC
    assert first.sent_at == datetime(2026, 3, 13, 3, 46, tzinfo=UTC)

    multi = parsed.messages[3]
    assert multi.text == "Long day.\nMeeting ran late\nand the bus was full"


def test_mdy_12h_export_with_narrow_spaces_bom_and_lrm() -> None:
    parsed = parse_export(read("android_mdy_12h.txt"), IST)
    assert parsed.date_order == DateOrder.MDY
    assert parsed.authors == ["Alex", "Sam"]
    by_text = {m.text: m.sent_at.astimezone(IST) for m in parsed.messages}
    assert by_text["Good morning!"] == datetime(2026, 3, 13, 9, 16, tzinfo=IST)
    assert by_text["just after midnight"] == datetime(2026, 3, 14, 0, 5, tzinfo=IST)
    assert by_text["lunch time"] == datetime(2026, 3, 14, 12, 30, tzinfo=IST)
    assert by_text["lowercase pm"] == datetime(2026, 3, 14, 21, 40, tzinfo=IST)
    assert parsed.messages[-1].text == "‎image omitted"  # normalized later, at ingest


def test_ios_bracket_format_with_seconds() -> None:
    parsed = parse_export("[13/03/26, 9:41:05 PM] Sam: hello\n", IST)
    assert parsed.messages[0].sent_at.astimezone(IST) == datetime(
        2026, 3, 13, 21, 41, 5, tzinfo=IST
    )


def test_dotted_dates_and_two_digit_years() -> None:
    parsed = parse_export("31.12.25, 23:59 - Sam: happy new year soon\n", IST)
    assert parsed.messages[0].sent_at.astimezone(IST) == datetime(2025, 12, 31, 23, 59, tzinfo=IST)


def test_ambiguous_dates_need_explicit_order() -> None:
    with pytest.raises(ExportParseError, match="dmy"):
        parse_export(read("ambiguous_dates.txt"), IST)

    parsed = parse_export(read("ambiguous_dates.txt"), IST, DateOrder.DMY)
    assert parsed.messages[0].sent_at.astimezone(IST).date() == datetime(2026, 2, 1).date()
    parsed = parse_export(read("ambiguous_dates.txt"), IST, DateOrder.MDY)
    assert parsed.messages[0].sent_at.astimezone(IST).date() == datetime(2026, 1, 2).date()


def test_wrong_explicit_order_is_a_clear_error() -> None:
    with pytest.raises(ExportParseError, match="line 2"):
        parse_export(read("android_dmy_24h.txt"), IST, DateOrder.MDY)


@pytest.mark.parametrize(
    "dates,order",
    [
        (["13/03/2026", "01/02/2026"], DateOrder.DMY),
        (["3/13/26", "1/2/26"], DateOrder.MDY),
        (["2026-03-13"], DateOrder.YMD),
    ],
)
def test_detect_date_order(dates: list[str], order: DateOrder) -> None:
    assert detect_date_order(dates) == order


def test_inconsistent_dates_rejected() -> None:
    with pytest.raises(ExportParseError, match="inconsistent"):
        detect_date_order(["13/01/2026", "01/13/2026"])


def test_not_an_export() -> None:
    with pytest.raises(ExportParseError, match="no WhatsApp messages"):
        parse_export("just some notes\nnothing here\n", IST)


def test_map_authors() -> None:
    parsed = parse_export(read("android_dmy_24h.txt"), IST)
    labeled = map_authors(parsed, my_author_name="Alex", contact_author_name="Sam")
    assert [s for s, m in labeled if m.author == "Sam"] == [Sender.CONTACT] * 5
    assert [s for s, m in labeled if m.author == "Alex"] == [Sender.ME] * 4


def test_map_authors_unknown_lists_names_found() -> None:
    parsed = parse_export(read("android_dmy_24h.txt"), IST)
    with pytest.raises(ExportParseError, match=r"authors in this file: \['Alex', 'Sam'\]"):
        map_authors(parsed, my_author_name="Me", contact_author_name="Sam")
