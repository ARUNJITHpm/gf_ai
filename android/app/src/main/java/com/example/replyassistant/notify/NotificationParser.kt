package com.example.replyassistant.notify

import com.example.replyassistant.net.IngestMessage
import com.example.replyassistant.net.IngestRequest
import com.example.replyassistant.screen.WhatsAppScreenParser
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** What we need from a WhatsApp notification, as plain data (testable without Android). */
data class NotificationInput(
    val isGroupSummary: Boolean,
    val isGroupConversation: Boolean,
    val title: String?,
    val userName: String?,
    val messages: List<NotificationMessage>,
)

/** `senderName == null` means the message is mine (MessagingStyle uses a null person). */
data class NotificationMessage(val senderName: String?, val text: String, val timestampMs: Long)

object NotificationParser {
    private val ISO = DateTimeFormatter.ISO_OFFSET_DATE_TIME

    /**
     * Returns an /ingest payload for the configured chat, or null to ignore the notification.
     *
     * Order matters: group conversations are dropped *before* titles are compared, because a
     * group's title changes with every message ("Family (3 messages): Amma"), see
     * spikes/notes.md.
     */
    fun toIngest(
        input: NotificationInput,
        configuredTitle: String,
        zone: ZoneId = ZoneId.systemDefault(),
    ): IngestRequest? {
        if (input.isGroupSummary || input.isGroupConversation) return null
        if (input.messages.isEmpty()) return null
        val title = input.title ?: return null
        if (!WhatsAppScreenParser.sameTitle(title, configuredTitle)) return null

        val messages = input.messages
            .filter { it.text.isNotBlank() }
            .map { message ->
                val mine = message.senderName == null ||
                    (input.userName != null && message.senderName == input.userName)
                IngestMessage(
                    sender = if (mine) "me" else "contact",
                    text = message.text,
                    sentAt = ISO.format(Instant.ofEpochMilli(message.timestampMs).atZone(zone)),
                    source = "notification",
                )
            }
        if (messages.isEmpty()) return null
        return IngestRequest(contactHint = title, messages = messages)
    }
}
