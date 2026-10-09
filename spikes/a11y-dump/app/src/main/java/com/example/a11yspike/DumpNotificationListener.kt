package com.example.a11yspike

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * Phase 0 spike (P0.5): logs WhatsApp notifications so we can see what
 * MessagingStyle exposes (sender, text, timestamp, group flag).
 */
class DumpNotificationListener : NotificationListenerService() {

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName != DumpAccessibilityService.WHATSAPP) return
        val n = sbn.notification
        val extras = n.extras
        val isSummary = n.flags and Notification.FLAG_GROUP_SUMMARY != 0
        val isGroup = extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION, false)

        Log.i(
            TAG,
            "POSTED key=${sbn.key} postTime=${sbn.postTime} summary=$isSummary " +
                "groupConv=$isGroup title=\"${r(extras.getCharSequence(Notification.EXTRA_TITLE))}\" " +
                "convTitle=\"${r(extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE))}\" " +
                "text=\"${r(extras.getCharSequence(Notification.EXTRA_TEXT))}\"",
        )

        val style = NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(n)
        if (style == null) {
            Log.i(TAG, "  (no MessagingStyle)")
            return
        }
        Log.i(TAG, "  user=\"${r(style.user.name)}\" messages=${style.messages.size}")
        for (m in style.messages) {
            Log.i(TAG, "  msg time=${m.timestamp} sender=\"${r(m.person?.name)}\" text=\"${r(m.text)}\"")
        }
    }

    private fun r(s: CharSequence?): String = when {
        s == null -> "null"
        DumpAccessibilityService.REDACT -> "<${s.length} chars>"
        else -> s.toString().replace("\n", "\\n")
    }

    private companion object {
        const val TAG = "A11ySpikeNotif"
    }
}
