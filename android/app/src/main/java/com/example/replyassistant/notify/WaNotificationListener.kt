package com.example.replyassistant.notify

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat
import com.example.replyassistant.data.Prefs
import com.example.replyassistant.outbox.Outbox
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/** Forwards messages from the configured WhatsApp chat's notifications to /ingest. */
class WaNotificationListener : NotificationListenerService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName !in WHATSAPP_PACKAGES) return
        val prefs = Prefs(this)
        if (prefs.paused || prefs.chatTitle.isBlank()) return

        val notification = sbn.notification
        val extras = notification.extras
        val style = NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(notification)
        val input = NotificationInput(
            isGroupSummary = notification.flags and Notification.FLAG_GROUP_SUMMARY != 0,
            isGroupConversation = extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION, false) ||
                style?.isGroupConversation == true,
            title = (style?.conversationTitle ?: extras.getCharSequence(Notification.EXTRA_TITLE))?.toString(),
            userName = style?.user?.name?.toString(),
            messages = style?.messages.orEmpty().map {
                NotificationMessage(
                    senderName = it.person?.name?.toString(),
                    text = it.text?.toString().orEmpty(),
                    timestampMs = it.timestamp,
                )
            },
        )
        val request = NotificationParser.toIngest(input, prefs.chatTitle) ?: return
        scope.launch { Outbox.enqueue(this@WaNotificationListener, request) }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private companion object {
        val WHATSAPP_PACKAGES = setOf("com.whatsapp", "com.whatsapp.w4b")
    }
}
