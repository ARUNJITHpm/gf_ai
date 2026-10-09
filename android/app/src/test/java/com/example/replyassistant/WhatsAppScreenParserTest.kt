package com.example.replyassistant

import com.example.replyassistant.screen.Bounds
import com.example.replyassistant.screen.ScreenNode
import com.example.replyassistant.screen.WhatsAppScreenParser
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WhatsAppScreenParserTest {
    /** Loads a fixture in the format written by the app's "dump next chat screen" button. */
    private fun load(name: String): Pair<ScreenNode, Int> {
        val text = javaClass.classLoader!!.getResource(name)!!.readText()
        val obj = Json.parseToJsonElement(text).jsonObject
        return toNode(obj["root"]!!.jsonObject) to obj["screenWidth"]!!.jsonPrimitive.int
    }

    private fun toNode(o: JsonObject): ScreenNode {
        val b = o["bounds"]?.jsonArray?.map { it.jsonPrimitive.int } ?: listOf(0, 0, 0, 0)
        return ScreenNode(
            viewId = o["viewId"]?.jsonPrimitive?.contentOrNull,
            className = o["className"]?.jsonPrimitive?.contentOrNull,
            text = o["text"]?.jsonPrimitive?.contentOrNull,
            bounds = Bounds(b[0], b[1], b[2], b[3]),
            editable = o["editable"]?.jsonPrimitive?.booleanOrNull ?: false,
            children = o["children"]?.jsonArray?.map { toNode(it.jsonObject) } ?: emptyList(),
        )
    }

    @Test
    fun parsesRealScreenStructure() {
        val (root, width) = load("screen_wa_2_26_39.json")
        val screen = WhatsAppScreenParser.parse(root, width)
        assertEquals("Asha 🌸", screen.title)
        assertTrue(screen.isChat)
        assertEquals(
            listOf(true to "hi", false to "hey!", true to "how was it", false to "long day, tell u later"),
            screen.messages.map { it.fromMe to it.text },
        )
        assertEquals(listOf("9:40 pm", "9:41 pm", "9:41 pm", "9:43 pm"), screen.messages.map { it.time })
    }

    @Test
    fun ignoresSystemRowsAndInputPlaceholder() {
        val (root, width) = load("screen_wa_2_26_39.json")
        val texts = WhatsAppScreenParser.parse(root, width).messages.map { it.text }
        assertFalse(texts.any { it.contains("encrypted") || it == "TODAY" || it == "Message" })
    }

    @Test
    fun ticksMeanMeEvenWhenBubbleIsWide() {
        // A long message of mine can span most of the width; the ticks still decide.
        val bubble = ScreenNode(
            viewId = "com.whatsapp:id/main_layout",
            bounds = Bounds(30, 0, 700, 50),
            children = listOf(
                ScreenNode(viewId = "com.whatsapp:id/message_text", text = "a very long message"),
                ScreenNode(viewId = "com.whatsapp:id/status"),
            ),
        )
        val screen = WhatsAppScreenParser.parse(ScreenNode(children = listOf(bubble)), 720)
        assertTrue(screen.messages.single().fromMe)
    }

    @Test
    fun notAChatWithoutInputBox() {
        val title = ScreenNode(viewId = "com.whatsapp:id/conversation_contact_name", text = "Asha")
        assertFalse(WhatsAppScreenParser.parse(ScreenNode(children = listOf(title)), 720).isChat)
    }

    @Test
    fun titleMatchingIgnoresCaseEmojiAndSpaces() {
        assertTrue(WhatsAppScreenParser.sameTitle("Asha 🌸", "asha"))
        assertTrue(WhatsAppScreenParser.sameTitle("  Asha   K ", "asha k"))
        assertTrue(WhatsAppScreenParser.sameTitle("Asha ❤️", "Asha"))
        assertFalse(WhatsAppScreenParser.sameTitle("Asha", "Ashanka"))
        assertFalse(WhatsAppScreenParser.sameTitle("🌸", "🌺"))
        assertFalse(WhatsAppScreenParser.sameTitle(null, "Asha"))
    }
}
