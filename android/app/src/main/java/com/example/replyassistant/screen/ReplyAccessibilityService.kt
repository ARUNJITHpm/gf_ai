package com.example.replyassistant.screen

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.example.replyassistant.data.Prefs
import com.example.replyassistant.net.ApiFactory
import com.example.replyassistant.net.FeedbackRequest
import com.example.replyassistant.net.IngestMessage
import com.example.replyassistant.net.IngestRequest
import com.example.replyassistant.net.SuggestRequest
import com.example.replyassistant.net.SuggestResponse
import com.example.replyassistant.outbox.Outbox
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Watches WhatsApp. When the configured chat is open it shows the bubble, sends visible
 * messages to the backend, fetches suggestions on request and types a chosen one into the
 * message box.
 *
 * It NEVER sends: the only action it performs on WhatsApp is ACTION_SET_TEXT on the input
 * field. A unit test (NoSendActionTest) fails the build if a click action appears here.
 */
class ReplyAccessibilityService : AccessibilityService() {
    private val handler = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val refresh = Runnable { onScreenSettled() }
    private lateinit var prefs: Prefs
    private lateinit var bubble: BubbleOverlay

    private var lastScreen: ParsedScreen? = null
    private var lastIngested: List<Pair<Boolean, String>> = emptyList()

    override fun onServiceConnected() {
        prefs = Prefs(this)
        bubble = BubbleOverlay(this)
        instance = this
        Log.i(TAG, "connected")
    }

    override fun onDestroy() {
        instance = null
        bubble.hide()
        scope.cancel()
        super.onDestroy()
    }

    override fun onInterrupt() = Unit

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (event.packageName?.toString() != WHATSAPP) return
        handler.removeCallbacks(refresh)
        handler.postDelayed(refresh, DEBOUNCE_MS)
    }

    private fun onScreenSettled() {
        val root = rootInActiveWindow ?: return
        val pkg = root.packageName?.toString()
        if (pkg == packageName) return // our own panel is in front; keep state
        if (pkg != WHATSAPP || prefs.paused || !prefs.isConfigured) {
            bubble.hide()
            return
        }
        val snapshot = NodeSnapshot.capture(root)
        if (dumpArmed) {
            dumpArmed = false
            writeDump(snapshot)
        }
        val screen = WhatsAppScreenParser.parse(snapshot, resources.displayMetrics.widthPixels)
        if (!screen.isChat || !WhatsAppScreenParser.sameTitle(screen.title, prefs.chatTitle)) {
            bubble.hide()
            return
        }
        lastScreen = screen
        bubble.show()
        ingestIfChanged(screen)
    }

    private fun ingestIfChanged(screen: ParsedScreen) {
        val visible = screen.messages.map { it.fromMe to it.text }
        if (visible.isEmpty() || visible == lastIngested) return
        lastIngested = visible
        val request = IngestRequest(
            contactHint = screen.title ?: return,
            messages = visible.map { (mine, text) -> screenMessage(mine, text) },
        )
        scope.launch(Dispatchers.IO) { Outbox.enqueue(this@ReplyAccessibilityService, request) }
    }

    /** Called by the bubble panel. */
    suspend fun fetchSuggestions(tone: String?): Result<SuggestResponse> {
        val screen = lastScreen ?: return Result.failure(IllegalStateException("Open the chat first"))
        val api = ApiFactory.create(prefs.backendUrl, prefs.apiKey, timeoutSeconds = 30)
        return withContext(Dispatchers.IO) {
            runCatching {
                api.suggest(
                    SuggestRequest(
                        contactHint = screen.title ?: prefs.chatTitle,
                        visibleMessages = screen.messages.map { screenMessage(it.fromMe, it.text) },
                        tone = tone?.takeIf { it.isNotBlank() },
                    ),
                )
            }
        }
    }

    /** Puts [text] into WhatsApp's message box. Does not send. */
    fun insertText(text: String, suggestionId: Int?, index: Int): Boolean {
        val root = rootInActiveWindow ?: return false
        val input = root.findAccessibilityNodeInfosByViewId("$WHATSAPP:id/${WhatsAppScreenParser.ID_INPUT}")
            .firstOrNull { it.isEditable } ?: findEditable(root) ?: return false
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = input.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        if (ok && suggestionId != null) {
            val api = ApiFactory.create(prefs.backendUrl, prefs.apiKey)
            scope.launch(Dispatchers.IO) {
                runCatching { api.feedback(suggestionId, FeedbackRequest(index)) }
            }
        }
        return ok
    }

    private fun findEditable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable) return node
        for (i in 0 until node.childCount) {
            node.getChild(i)?.let { child -> findEditable(child)?.let { return it } }
        }
        return null
    }

    private fun writeDump(snapshot: ScreenNode) {
        val dir = File(filesDir, "dumps").apply { mkdirs() }
        val file = File(dir, "screen-${System.currentTimeMillis()}.json")
        file.writeText(NodeSnapshot.toJson(snapshot, keepText = false).toString(2))
        Log.i(TAG, "screen dump written: ${file.absolutePath}")
    }

    private fun screenMessage(mine: Boolean, text: String) = IngestMessage(
        sender = if (mine) "me" else "contact",
        text = text,
        source = "screen",
    )

    companion object {
        const val WHATSAPP = "com.whatsapp"
        private const val TAG = "ReplyA11y"
        private const val DEBOUNCE_MS = 600L

        @Volatile var instance: ReplyAccessibilityService? = null
            private set

        /** Set from the settings screen; the next WhatsApp screen is saved (text redacted). */
        @Volatile var dumpArmed = false
    }
}
