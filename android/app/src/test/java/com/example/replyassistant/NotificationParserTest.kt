package com.example.replyassistant

import com.example.replyassistant.notify.NotificationInput
import com.example.replyassistant.notify.NotificationMessage
import com.example.replyassistant.notify.NotificationParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.ZoneId

class NotificationParserTest {
    private val ist = ZoneId.of("Asia/Kolkata")
    private val t1 = 1791563041000L // values seen in the Phase 0 spike logs
    private val t2 = 1791563059000L

    private fun input(
        title: String? = "Nithara",
        group: Boolean = false,
        summary: Boolean = false,
        messages: List<NotificationMessage> = listOf(
            NotificationMessage("Nithara", "first", t1),
            NotificationMessage("Nithara", "second", t2),
        ),
    ) = NotificationInput(summary, group, title, userName = "You", messages = messages)

    @Test
    fun oneToOneMessagesWithTimestamps() {
        val request = NotificationParser.toIngest(input(), "nithara", ist)!!
        assertEquals("Nithara", request.contactHint)
        assertEquals(listOf("first", "second"), request.messages.map { it.text })
        assertEquals(listOf("contact", "contact"), request.messages.map { it.sender })
        assertEquals("2026-10-09T21:54:01+05:30", request.messages[0].sentAt)
        assertEquals("notification", request.messages[0].source)
    }

    @Test
    fun myRepliesAreMe() {
        val messages = listOf(
            NotificationMessage("Nithara", "hi", t1),
            NotificationMessage(null, "hey (replied from the notification)", t2),
            NotificationMessage("You", "also me", t2),
        )
        val request = NotificationParser.toIngest(input(messages = messages), "Nithara", ist)!!
        assertEquals(listOf("contact", "me", "me"), request.messages.map { it.sender })
    }

    @Test
    fun groupsAreDroppedBeforeTitleMatching() {
        // A group's title changes with each message, e.g. "Nithara (2 messages): Amma".
        assertNull(NotificationParser.toIngest(input(title = "Nithara", group = true), "Nithara", ist))
    }

    @Test
    fun summariesOtherChatsAndEmptyAreDropped() {
        assertNull(NotificationParser.toIngest(input(summary = true), "Nithara", ist))
        assertNull(NotificationParser.toIngest(input(title = "Rahul"), "Nithara", ist))
        assertNull(NotificationParser.toIngest(input(title = null), "Nithara", ist))
        assertNull(NotificationParser.toIngest(input(messages = emptyList()), "Nithara", ist))
        val blank = listOf(NotificationMessage("Nithara", " ", t1))
        assertNull(NotificationParser.toIngest(input(messages = blank), "Nithara", ist))
    }
}
