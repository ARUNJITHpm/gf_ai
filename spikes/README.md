# Phase 0 spike — run guide

Throwaway app (`a11y-dump/`) to prove that, on your phone, we can:
1. read WhatsApp chat messages and their left/right position (P0.3),
2. insert text into the message box without sending (P0.4),
3. read WhatsApp notifications with sender + timestamp (P0.5).

It never sends messages. It only logs to logcat on your PC.

## 1. Build and install

Phone: Settings → About phone → tap "Build number" 7× → Developer options → enable **USB debugging**. Connect by USB and accept the prompt.

```bash
cd spikes/a11y-dump
./gradlew installDebug          # Windows cmd/PowerShell: .\gradlew installDebug
adb devices                     # should list your phone
```

## 2. Grant permissions

Open the **A11y Spike** app:
- **Open Accessibility settings** → Installed apps → *A11y Spike (WhatsApp dump)* → ON.
  - Android 13+: if it's greyed out ("Restricted setting"), go to Settings → Apps → A11y Spike → ⋮ menu (top right) → **Allow restricted settings**, then try again.
- **Open Notification access settings** → *A11y Spike notifications* → Allow.
- Back in the app, the status should say `Accessibility service: ON`.

## 3. Watch logs

On the PC, in a separate terminal:

```bash
adb logcat -c && adb logcat -s A11ySpike A11ySpikeNotif
```

## 4. Tests

**P0.3 — screen dump.** Open a chat in WhatsApp and wait about a second. You should see a `DUMP start … DUMP end` block. Check:
- [ ] message texts appear as `text="…"`
- [ ] your messages show `side=RIGHT(me?)`, hers `side=LEFT(them?)`
- [ ] the chat title (contact name) appears near the top
- [ ] there is an `EDITABLE` node (the input box), note its `id=` (expected: `entry`)
- [ ] timestamps of bubbles appear (note their `id=`, e.g. `date`)

**P0.4 — insert.** In the spike app tap **Arm insert test**, then open a WhatsApp chat.
- [ ] `test from spike` appears in the input box
- [ ] log shows `INSERT: … ok=true`
- [ ] you can edit it and send/delete it by hand (nothing was sent automatically)

**P0.5 — notifications.** Leave WhatsApp (or lock the phone) and have someone message you (two messages in a row is best).
- [ ] `POSTED … groupConv=false` line with the chat title
- [ ] `msg time=… sender="…" text="…"` lines, one per message
- [ ] a second notification re-lists the earlier messages (expected; the backend dedupes)
- [ ] group chats show `groupConv=true`

## 5. Record results

Tick the **Redact message text in logs** box in the app, repeat one dump and one notification, and paste the redacted output into `spikes/notes.md`. Never paste unredacted chats into the repo.

If something fails (e.g. no message texts in the tree), stop there. That changes the Android design, so we revisit §11 before building the backend.
