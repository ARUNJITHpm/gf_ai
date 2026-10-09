# Phase 0 spike results

**Redacted logs only** — text replaced by `<Nch>` (character count).

- Phone model / Android version: Xiaomi Redmi 9 (M2006C3MI), Android 10, MIUI 12, screen 720 px wide
- WhatsApp version: 2.26.39.74
- Date tested: 2026-10-09

## Setup notes (Xiaomi / MIUI)
- `adb install` blocked (`INSTALL_FAILED_USER_RESTRICTED`) unless "Install via USB" is on, which needs a Mi account. Workaround: `adb push` the APK to `/sdcard/Download/` and install from File Manager.
- In Git Bash, prefix adb commands that use phone paths with `MSYS_NO_PATHCONV=1`.
- Accessibility can't be enabled over adb (`WRITE_SECURE_SETTINGS` denied). Enable by hand: Settings → Additional settings → Accessibility → Downloaded apps.
- Notification access *can* be granted over adb: `adb shell cmd notification allow_listener <pkg>/<service>`.
- The USB connection drops now and then; record logcat in a reconnect loop.

## P0.3 Screen dump — PASS
- Message texts visible: **yes** (`id=message_text`)
- Left/right side correct: **yes**, measured on the bubble container `id=main_layout`
- Extra "me" signal: only my bubbles have `ImageView id=status` (ticks)
- Input box id: `com.whatsapp:id/entry` (EditText; placeholder text when empty)
- Chat title id: `com.whatsapp:id/conversation_contact_name`
- Bubble timestamp id: `com.whatsapp:id/date` (time only, e.g. "9:41 pm"; the day comes from `conversation_row_date_divider`)
- Edited marker: `com.whatsapp:id/edit_label`
- Send/mic button: `send_container` / `voice_note_btn` — **never touch**
- Lesson for `WhatsAppScreenParser`: decide side from `main_layout` bounds, not child nodes. On a left bubble, `date_wrapper` sits right of center and would be misread as "me".

```
ListView id=list
  TextView id=conversation_row_date_divider bounds=[298,181][422,249] text=<5ch>
  Button id=info bounds=[74,258][646,435] text=<118ch>            # encryption notice (system)
  ViewGroup id=conversation_row_text bounds=[0,438][720,531]
    LinearLayout id=main_layout bounds=[454,448][686,521]           # RIGHT -> me
      TextView id=message_text text=<2ch>
      TextView id=date text=<7ch>
      ImageView id=status desc=<4ch>
  ViewGroup id=conversation_row_text bounds=[0,531][720,624]
    LinearLayout id=main_layout bounds=[34,541][236,614]            # LEFT -> contact
      TextView id=message_text text=<2ch>
      TextView id=date text=<7ch>
  ViewGroup id=conversation_row_text bounds=[0,717][720,810]
    LinearLayout id=main_layout bounds=[34,727][535,800]            # LEFT -> contact (edited)
      TextView id=message_text text=<14ch>
      LinearLayout id=date_wrapper bounds=[315,758][531,796]        # right of center! ignore for side
        TextView id=edit_label text=<6ch>
        TextView id=date text=<7ch>
EditText id=entry bounds=[112,912][301,985] EDITABLE text=<7ch>     # placeholder
FrameLayout id=send_container / ImageButton id=voice_note_btn       # never touch
```

## P0.4 Insert text — PASS
- `ACTION_SET_TEXT` on `id/entry` → `ok=true`
- Nothing sent automatically

## P0.5 Notifications — PASS (group flag deferred to P6.4)
- MessagingStyle present: **yes**
- Per-message timestamps: **yes** (`msg time` is the send time in epoch ms; `postTime` is ~1 s later)
- Sender name: **yes** (the contact's saved name); `user` = "You"
- Each message also posts a **group summary** notification (`summary=true`, no MessagingStyle) → ignore
- Multi-message re-post: **confirmed**. The 2nd notification reuses the same `key` and re-lists message 1 with an identical `msg time` → dedupe on (sender, text, time) is reliable for this source
- Group flag works: _not tested (no group on test number) — verify with a fixture in P6.4_

```
POSTED summary=false groupConv=false title=<t>
  user=<u> messages=1
  msg time=1791562371000 sender=<n> text=<r>
POSTED summary=true groupConv=false title=<t> text=<r>
  (no MessagingStyle)

# two messages in a row (same notification key both times)
POSTED summary=false groupConv=false title=<t> text=<18ch>
  user=<u> messages=1
  msg time=1791563041000 sender=<n> text=<18ch>
POSTED summary=false groupConv=false title=<t> text=<19ch>
  user=<u> messages=2
  msg time=1791563041000 sender=<n> text=<18ch>     # re-listed, same timestamp
  msg time=1791563059000 sender=<n> text=<19ch>
```

## Verdict
- [x] P0.3 screen reading
- [x] P0.4 text insert
- [x] P0.5 notifications (group flag check deferred to P6.4)
- [x] Gate P0 passed, go to Phase 1
