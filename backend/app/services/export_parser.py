"""Parser for WhatsApp "Export chat" .txt files. Pure functions, no database.

Handles the common Android formats (and the bracketed iOS one):
    31/12/2023, 21:41 - Name: text
    12/31/23, 9:41 PM - Name: text          (often with U+202F before PM)
    31.12.23, 21:41 - Name: text
    [31/12/23, 9:41:05 PM] Name: text       (iOS)
Lines that don't start with a date continue the previous message. Lines with a date but no
"Name: " are system lines (encryption notice, number changes, ...) and are counted, not kept.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.models.enums import Sender

_HEADER = re.compile(
    r"""^‎?\[?
    (?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+
    (?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?)
    (?:[\s  ]*(?P<ampm>[AaPp]\.?\s?[Mm]\.?))?
    (?:\]\s|\s[-–]\s)
    (?P<rest>.*)$""",
    re.VERBOSE,
)
_DATE_PARTS = re.compile(r"[./-]")
# Author names are short and single-line; anything longer before ": " is a system line.
_MAX_AUTHOR_LEN = 80


class DateOrder(StrEnum):
    AUTO = "auto"
    DMY = "dmy"
    MDY = "mdy"
    YMD = "ymd"


class ExportParseError(ValueError):
    """The file can't be parsed as asked. The message is safe to show (no chat text)."""


@dataclass
class ParsedMessage:
    author: str
    text: str
    sent_at: datetime  # UTC
    line_no: int


@dataclass
class ParsedExport:
    messages: list[ParsedMessage] = field(default_factory=list)
    system_lines: int = 0
    date_order: DateOrder = DateOrder.AUTO

    @property
    def authors(self) -> list[str]:
        return sorted({m.author for m in self.messages})


@dataclass
class _RawHeader:
    date: str
    time: str
    ampm: str | None
    rest: str
    line_no: int


def parse_export(
    content: str, tz: ZoneInfo, date_order: DateOrder = DateOrder.AUTO
) -> ParsedExport:
    content = content.lstrip("﻿")
    headers: list[_RawHeader] = []
    continuations: dict[int, list[str]] = {}  # index in headers -> extra lines

    for line_no, line in enumerate(content.splitlines(), start=1):
        match = _HEADER.match(line)
        if match:
            headers.append(
                _RawHeader(
                    date=match["date"],
                    time=match["time"],
                    ampm=match["ampm"],
                    rest=match["rest"],
                    line_no=line_no,
                )
            )
        elif headers:
            continuations.setdefault(len(headers) - 1, []).append(line)
        # Lines before the first header (e.g. a stray BOM line) are ignored.

    if not headers:
        raise ExportParseError("no WhatsApp messages found; is this an exported chat .txt?")

    order = (
        detect_date_order([h.date for h in headers]) if date_order == DateOrder.AUTO else date_order
    )
    result = ParsedExport(date_order=order)

    for index, header in enumerate(headers):
        author, sep, first_line = header.rest.partition(": ")
        if not sep or not author or len(author) > _MAX_AUTHOR_LEN:
            result.system_lines += 1
            continue
        text = "\n".join([first_line, *continuations.get(index, [])])
        local = _to_datetime(header, order, tz)
        result.messages.append(
            ParsedMessage(
                author=author.lstrip("‎").strip(),
                text=text,
                sent_at=local.astimezone(UTC),
                line_no=header.line_no,
            )
        )
    return result


def detect_date_order(dates: Iterable[str]) -> DateOrder:
    first_big = second_big = year_first = False
    for date in dates:
        a, b, _c = _DATE_PARTS.split(date)
        if len(a) == 4:
            year_first = True
        elif int(a) > 12:
            first_big = True
        elif int(b) > 12:
            second_big = True
    if year_first:
        return DateOrder.YMD
    if first_big and second_big:
        raise ExportParseError("dates are inconsistent (both day-first and month-first seen)")
    if first_big:
        return DateOrder.DMY
    if second_big:
        return DateOrder.MDY
    raise ExportParseError(
        "can't tell day/month order from these dates (all days are 12 or less); "
        "choose date_order 'dmy' or 'mdy'"
    )


def map_authors(
    parsed: ParsedExport, my_author_name: str, contact_author_name: str
) -> list[tuple[Sender, ParsedMessage]]:
    """Label each message `me` or `contact`. Any other author is an error listing all authors."""
    mapping = {my_author_name.strip(): Sender.ME, contact_author_name.strip(): Sender.CONTACT}
    unknown = [author for author in parsed.authors if author not in mapping]
    if unknown:
        raise ExportParseError(
            f"unexpected author(s) {unknown}; authors in this file: {parsed.authors}. "
            "Group chats are not supported."
        )
    return [(mapping[m.author], m) for m in parsed.messages]


def _to_datetime(header: _RawHeader, order: DateOrder, tz: ZoneInfo) -> datetime:
    a, b, c = (int(part) for part in _DATE_PARTS.split(header.date))
    if order == DateOrder.YMD:
        year, month, day = a, b, c
    elif order == DateOrder.DMY:
        day, month, year = a, b, c
    else:
        month, day, year = a, b, c
    if year < 100:
        year += 2000

    time_parts = [int(part) for part in re.split(r"[:.]", header.time)]
    hour, minute = time_parts[0], time_parts[1]
    second = time_parts[2] if len(time_parts) > 2 else 0
    if header.ampm:
        is_pm = header.ampm.lower().startswith("p")
        hour = hour % 12 + (12 if is_pm else 0)

    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError as exc:
        raise ExportParseError(
            f"invalid date/time on line {header.line_no} with order {order.value}"
        ) from exc
