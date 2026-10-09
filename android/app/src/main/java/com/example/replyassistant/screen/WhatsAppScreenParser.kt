package com.example.replyassistant.screen

/**
 * Turns a snapshot of WhatsApp's chat screen into (title, ordered messages).
 *
 * ALL knowledge of WhatsApp's view structure lives in this class. WhatsApp updates will
 * break it; when that happens, capture a new tree with the debug "Dump screen" button,
 * add it as a test fixture, and fix the ids/rules below.
 *
 * Structure observed on WhatsApp 2.26.39 (see spikes/notes.md):
 *
 *   TextView  id=conversation_contact_name        chat title in the toolbar
 *   ListView  id=list
 *     ViewGroup id=conversation_row_text           one row per message
 *       LinearLayout id=main_layout                 the bubble (decides left/right!)
 *         TextView id=message_text                  message body
 *         TextView id=date                          "9:41 pm"
 *         ImageView id=status                       ticks: only on MY messages
 *         TextView id=edit_label                    "Edited"
 *   EditText  id=entry                             the message input box
 *   send_container / voice_note_btn                the Send button: NEVER touched
 *
 * Side rule: decide me/them from the bubble's position, not from its children. On a
 * left bubble the time label can sit right of centre and would be misread as "me".
 * A `status` (ticks) icon inside the bubble also means "me" and wins when present.
 */
object WhatsAppScreenParser {
    const val ID_TITLE = "conversation_contact_name"
    const val ID_MESSAGE_TEXT = "message_text"
    const val ID_BUBBLE = "main_layout"
    const val ID_STATUS = "status"
    const val ID_TIME = "date"
    const val ID_INPUT = "entry"

    fun parse(root: ScreenNode, screenWidth: Int): ParsedScreen {
        val title = findFirst(root) { it.shortId == ID_TITLE }?.text?.trim()
        val messages = mutableListOf<ScreenMessage>()
        collectMessages(root, ancestors = emptyList(), screenWidth = screenWidth, out = messages)
        messages.sortWith(compareBy({ it.top }, { it.left }))
        val hasInput = findFirst(root) { it.shortId == ID_INPUT && it.editable } != null
        return ParsedScreen(title = title, messages = messages, isChat = hasInput && title != null)
    }

    private fun collectMessages(
        node: ScreenNode,
        ancestors: List<ScreenNode>,
        screenWidth: Int,
        out: MutableList<ScreenMessage>,
    ) {
        if (node.shortId == ID_MESSAGE_TEXT && !node.text.isNullOrBlank()) {
            val bubble = ancestors.lastOrNull { it.shortId == ID_BUBBLE } ?: node
            val hasTicks = findFirst(bubble) { it.shortId == ID_STATUS } != null
            val leftGap = bubble.bounds.left
            val rightGap = screenWidth - bubble.bounds.right
            val fromMe = hasTicks || leftGap > rightGap
            val time = findFirst(bubble) { it.shortId == ID_TIME }?.text?.trim()
            out += ScreenMessage(
                fromMe = fromMe,
                text = node.text.trim(),
                time = time,
                top = bubble.bounds.top,
                left = bubble.bounds.left,
            )
            return // a bubble's other text (quotes, labels) is not a separate message
        }
        val path = ancestors + node
        for (child in node.children) collectMessages(child, path, screenWidth, out)
    }

    private fun findFirst(node: ScreenNode, match: (ScreenNode) -> Boolean): ScreenNode? {
        if (match(node)) return node
        for (child in node.children) findFirst(child, match)?.let { return it }
        return null
    }

    /** Case-, emoji- and whitespace-insensitive title comparison (mirrors the backend). */
    fun sameTitle(a: String?, b: String?): Boolean {
        if (a == null || b == null) return false
        val na = normalizeTitle(a)
        return na.isNotEmpty() && na == normalizeTitle(b)
    }

    fun normalizeTitle(title: String): String {
        val kept = buildString {
            var i = 0
            while (i < title.length) {
                val cp = title.codePointAt(i)
                val type = Character.getType(cp)
                val drop = type == Character.OTHER_SYMBOL.toInt() ||
                    type == Character.MODIFIER_SYMBOL.toInt() ||
                    type == Character.FORMAT.toInt() ||
                    cp == 0xFE0E || cp == 0xFE0F
                if (!drop) appendCodePoint(cp)
                i += Character.charCount(cp)
            }
        }
        return kept.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
    }
}

/** Plain copy of an accessibility node, so parsing is testable without a device. */
data class ScreenNode(
    val viewId: String? = null,
    val className: String? = null,
    val text: String? = null,
    val bounds: Bounds = Bounds(0, 0, 0, 0),
    val editable: Boolean = false,
    val children: List<ScreenNode> = emptyList(),
) {
    /** "com.whatsapp:id/entry" -> "entry" */
    val shortId: String? get() = viewId?.substringAfter(":id/")
}

data class Bounds(val left: Int, val top: Int, val right: Int, val bottom: Int)

data class ScreenMessage(
    val fromMe: Boolean,
    val text: String,
    val time: String?,
    val top: Int,
    val left: Int,
)

data class ParsedScreen(
    val title: String?,
    val messages: List<ScreenMessage>,
    val isChat: Boolean,
)
