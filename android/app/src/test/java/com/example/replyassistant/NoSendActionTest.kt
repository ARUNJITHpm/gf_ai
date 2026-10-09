package com.example.replyassistant

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The app must never send a WhatsApp message. The only action it may perform on another
 * app's UI is ACTION_SET_TEXT. This scans the source so a click on WhatsApp's Send button
 * can't slip in unnoticed.
 */
class NoSendActionTest {
    private val sources: List<File> =
        File("src/main/java").walkTopDown().filter { it.extension == "kt" }.toList()

    @Test
    fun sourcesFound() {
        assertTrue("run from the app module directory", sources.size > 5)
    }

    @Test
    fun onlySetTextActionsArePerformed() {
        val offenders = sources.flatMap { file ->
            file.readLines().mapIndexedNotNull { i, line ->
                val code = line.substringBefore("//")
                val bad = (code.contains("performAction(") && !code.contains("ACTION_SET_TEXT")) ||
                    code.contains("ACTION_CLICK") ||
                    code.contains("performGlobalAction") ||
                    code.contains("dispatchGesture")
                if (bad) "${file.name}:${i + 1}: ${line.trim()}" else null
            }
        }
        assertTrue("Forbidden UI actions:\n" + offenders.joinToString("\n"), offenders.isEmpty())
    }

    @Test
    fun sendButtonIdsOnlyAppearInComments() {
        val offenders = sources.flatMap { file ->
            file.readLines().mapIndexedNotNull { i, line ->
                val code = line.trim()
                val isComment = code.startsWith("*") || code.startsWith("//") || code.startsWith("/*")
                val mentionsSend = code.contains("send_container") || code.contains("voice_note_btn")
                if (!isComment && mentionsSend) "${file.name}:${i + 1}" else null
            }
        }
        assertTrue("Send button referenced in code: $offenders", offenders.isEmpty())
    }
}
