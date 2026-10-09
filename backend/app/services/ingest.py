"""Normalize, dedupe and store incoming messages (spec §6).

Used by /ingest (notifications, screen reads), the WhatsApp export importer and the CLI.

Dedupe in short:
- The hash covers contact, sender and normalized text, never the time, because each
  source reports time differently (export: minute, notification: second, screen: often none).
- Timestamped messages match an existing row with the same hash within ±2 minutes
  (or a row without sent_at observed within ±10 minutes). Each stored row can match only
  one incoming message, so two real "ok"s in the same minute stay two rows.
- Messages without a timestamp (screen reads) are aligned as a sequence against the tail of
  the stored conversation, and only the part that isn't already stored is inserted.
"""

import bisect
import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_utils import log_event
from app.models import Contact, Message
from app.models.base import utcnow
from app.models.enums import MessageKind, MessageSource, Sender

TIMESTAMP_WINDOW = timedelta(minutes=2)
OBSERVED_WINDOW = timedelta(minutes=10)
ALIGN_TAIL = 100
_HASH_CHUNK = 500

# Invisible characters WhatsApp and Android sprinkle into text: direction marks,
# zero-width characters, BOM, bidi isolates/embeddings.
_INVISIBLE = dict.fromkeys(
    map(ord, "​‌‎‏﻿⁦⁧⁨⁩‪‫‬‭‮"),
    None,
)
# Narrow and normal no-break spaces become plain spaces.
_SPACES = {ord(" "): " ", ord(" "): " "}
_EDITED_SUFFIX = re.compile(r"\s*<This message was edited>\s*$", re.IGNORECASE)

# Known placeholder texts, all in one place. Compared after normalize_text + casefold.
MEDIA_PLACEHOLDERS = frozenset(
    {
        "<media omitted>",
        "image omitted",
        "video omitted",
        "audio omitted",
        "sticker omitted",
        "gif omitted",
        "document omitted",
        "contact card omitted",
        "null",  # view-once media in Android exports
        "sticker",  # notification text for a sticker
        "gif",
    }
)
MEDIA_PREFIXES = (
    "<attached:",  # iOS exports
    "📷 photo",
    "🎥 video",
    "🎤 voice message",
    "🎵 audio",
    "📄 ",
    "👤 contact",
    "📍 location",
    "location: https://maps.google.com",
)
DELETED_TEXTS = frozenset(
    {
        "this message was deleted",
        "you deleted this message",
        "this message was deleted by admin",
    }
)


def normalize_text(text: str) -> str:
    """Canonical form used for storage and hashing. Keeps line breaks and emojis."""
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE).translate(_SPACES)
    text = _EDITED_SUFFIX.sub("", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(lines).strip("\n")


def classify_kind(normalized: str) -> MessageKind:
    folded = normalized.casefold()
    if folded in DELETED_TEXTS:
        return MessageKind.DELETED
    if folded in MEDIA_PLACEHOLDERS or (
        "\n" not in folded and len(folded) <= 80 and folded.startswith(MEDIA_PREFIXES)
    ):
        return MessageKind.MEDIA
    return MessageKind.TEXT


def dedupe_hash(contact_id: int, sender: Sender, normalized: str) -> str:
    return hashlib.sha256(f"{contact_id}|{sender.value}|{normalized}".encode()).hexdigest()


# --- contact matching -------------------------------------------------------------------


def normalize_title(title: str) -> str:
    """Case-, emoji- and whitespace-insensitive form of a chat title.

    Only symbols (emoji), format characters and variation selectors are removed. Combining
    marks are kept because Indic scripts need them.
    """
    title = unicodedata.normalize("NFKC", title)
    kept = (
        ch for ch in title if unicodedata.category(ch) not in {"So", "Sk", "Cf"} and ch not in "︎️"
    )
    return " ".join("".join(kept).casefold().split())


def match_contact(session: Session, hint: str) -> Contact | None:
    wanted = normalize_title(hint)
    if not wanted:
        return None
    contacts = session.scalars(select(Contact).where(Contact.active).order_by(Contact.id))
    for contact in contacts:
        if any(normalize_title(title) == wanted for title in contact.wa_chat_titles):
            return contact
    return None


# --- ingest -----------------------------------------------------------------------------


@dataclass(frozen=True)
class IncomingMessage:
    sender: Sender
    text: str
    source: MessageSource
    sent_at: datetime | None = None  # timezone-aware


@dataclass
class IngestResult:
    inserted: int = 0
    skipped: int = 0
    updated: int = 0


@dataclass
class _Prepared:
    sender: Sender
    text: str
    kind: MessageKind
    hash: str
    source: MessageSource
    sent_at: datetime | None


def ingest_messages(
    session: Session,
    contact: Contact,
    messages: Sequence[IncomingMessage],
    *,
    now: datetime | None = None,
) -> IngestResult:
    """Store new messages, skip duplicates, fill in missing timestamps. Commits."""
    now = now or utcnow()
    result = IngestResult()

    prepared: list[_Prepared] = []
    for message in messages:
        text = normalize_text(message.text)
        if not text:
            result.skipped += 1
            continue
        prepared.append(
            _Prepared(
                sender=message.sender,
                text=text,
                kind=classify_kind(text),
                hash=dedupe_hash(contact.id, message.sender, text),
                source=message.source,
                sent_at=message.sent_at,
            )
        )

    timed = [p for p in prepared if p.sent_at is not None]
    untimed = [p for p in prepared if p.sent_at is None]
    if timed:
        _ingest_timed(session, contact, timed, now, result)
    if untimed:
        _ingest_untimed(session, contact, untimed, now, result)

    session.commit()
    log_event(
        "ingest.done",
        contact_id=contact.id,
        inserted=result.inserted,
        skipped=result.skipped,
        updated=result.updated,
    )
    return result


def _ingest_timed(
    session: Session,
    contact: Contact,
    batch: list[_Prepared],
    now: datetime,
    result: IngestResult,
) -> None:
    buckets: defaultdict[str, _Bucket] = defaultdict(_Bucket)
    for hash_, rows in _existing_by_hash(session, contact.id, {p.hash for p in batch}).items():
        for row in rows:
            buckets[hash_].add(row)
    claimed: set[int] = set()  # id() of rows already matched in this call (or just inserted)

    for item in sorted(batch, key=lambda p: p.sent_at or now):
        assert item.sent_at is not None
        bucket = buckets[item.hash]
        match = bucket.closest(item.sent_at, claimed)
        if match is not None:
            claimed.add(id(match))
            if match.sent_at is None:
                match.sent_at = item.sent_at
                result.updated += 1
            else:
                result.skipped += 1
            continue

        row = _new_row(contact.id, item, now)
        session.add(row)
        bucket.add(row)
        claimed.add(id(row))
        result.inserted += 1


class _Bucket:
    """Stored rows sharing one hash. Timed rows are kept sorted so a lookup only scans the
    ±2 minute window (short texts like "ok" can have thousands of rows)."""

    def __init__(self) -> None:
        self.times: list[datetime] = []
        self.timed: list[Message] = []
        self.untimed: list[Message] = []

    def add(self, row: Message) -> None:
        if row.sent_at is None:
            self.untimed.append(row)
            return
        index = bisect.bisect_right(self.times, row.sent_at)
        self.times.insert(index, row.sent_at)
        self.timed.insert(index, row)

    def closest(self, sent_at: datetime, claimed: set[int]) -> Message | None:
        best: tuple[timedelta, Message] | None = None
        start = bisect.bisect_left(self.times, sent_at - TIMESTAMP_WINDOW)
        for index in range(start, len(self.times)):
            if self.times[index] > sent_at + TIMESTAMP_WINDOW:
                break
            row = self.timed[index]
            gap = abs(self.times[index] - sent_at)
            if id(row) not in claimed and (best is None or gap < best[0]):
                best = (gap, row)
        for row in self.untimed:
            gap = abs(row.observed_at - sent_at)
            if (
                id(row) not in claimed
                and gap <= OBSERVED_WINDOW
                and (best is None or gap < best[0])
            ):
                best = (gap, row)
        return None if best is None else best[1]


def _existing_by_hash(
    session: Session, contact_id: int, hashes: set[str]
) -> defaultdict[str, list[Message]]:
    found: defaultdict[str, list[Message]] = defaultdict(list)
    hash_list = sorted(hashes)
    for start in range(0, len(hash_list), _HASH_CHUNK):
        chunk = hash_list[start : start + _HASH_CHUNK]
        rows = session.scalars(
            select(Message).where(Message.contact_id == contact_id, Message.dedupe_hash.in_(chunk))
        )
        for row in rows:
            found[row.dedupe_hash].append(row)
    return found


def _ingest_untimed(
    session: Session,
    contact: Contact,
    batch: list[_Prepared],
    now: datetime,
    result: IngestResult,
) -> None:
    """Screen reads: the visible messages, in order, without timestamps."""
    session.flush()  # include rows added earlier in this call
    tail = list(
        reversed(
            session.scalars(
                select(Message)
                .where(Message.contact_id == contact.id)
                .order_by(
                    func.coalesce(Message.sent_at, Message.observed_at).desc(), Message.id.desc()
                )
                .limit(ALIGN_TAIL)
            ).all()
        )
    )
    stored = [(row.sender, row.dedupe_hash) for row in tail]
    incoming = [(p.sender, p.hash) for p in batch]

    overlap = _overlap(stored, incoming)
    if overlap == 0 and stored:
        # Nothing lines up with the recent tail: either all new, or scrolled far back into
        # history. Insert only messages never seen before. (A genuinely new repeat of an
        # old text, like "ok", is lost here; notifications usually catch it anyway.)
        known = _existing_by_hash(session, contact.id, {p.hash for p in batch})
        new_items = [p for p in batch if not known[p.hash]]
    else:
        new_items = batch[overlap:]

    result.skipped += len(batch) - len(new_items)
    for item in new_items:
        session.add(_new_row(contact.id, item, now))
        result.inserted += 1


def _overlap(stored: list[tuple[Sender, str]], incoming: list[tuple[Sender, str]]) -> int:
    """How many leading incoming messages are already stored.

    Tries every start position in `stored` where `incoming` lines up (until either list
    runs out) and returns the longest such overlap, preferring the most recent position.
    """
    best = 0
    for start in range(len(stored)):
        length = min(len(incoming), len(stored) - start)
        if length >= best and stored[start : start + length] == incoming[:length]:
            best = length
    return best


def _new_row(contact_id: int, item: _Prepared, now: datetime) -> Message:
    return Message(
        contact_id=contact_id,
        sender=item.sender,
        text=item.text,
        kind=item.kind,
        dedupe_hash=item.hash,
        source=item.source,
        sent_at=item.sent_at,
        observed_at=now,
    )
