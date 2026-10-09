package com.example.a11yspike

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Phase 0 spike (P0.3 + P0.4).
 *
 * - Logs WhatsApp's node tree to logcat (tag "A11ySpike") after the screen settles.
 * - When an insert is armed from MainActivity, puts test text into the message box
 *   with ACTION_SET_TEXT. It never touches the send button.
 */
class DumpAccessibilityService : AccessibilityService() {

    private val handler = Handler(Looper.getMainLooper())
    private val dumpRunnable = Runnable { dumpTree() }

    override fun onServiceConnected() {
        instance = this
        Log.i(TAG, "service connected")
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (event.packageName != WHATSAPP) return
        // WhatsApp fires many content-changed events; dump once things are quiet.
        handler.removeCallbacks(dumpRunnable)
        handler.postDelayed(dumpRunnable, DEBOUNCE_MS)
    }

    override fun onInterrupt() = Unit

    private fun dumpTree() {
        val root = rootInActiveWindow ?: return
        val screenWidth = resources.displayMetrics.widthPixels
        Log.i(TAG, "===== DUMP start (screenWidth=$screenWidth, redact=$REDACT) =====")
        walk(root, 0, screenWidth)
        Log.i(TAG, "===== DUMP end =====")

        if (insertArmed) {
            insertArmed = false
            insertText(root, TEST_TEXT)
        }
    }

    private fun walk(node: AccessibilityNodeInfo, depth: Int, screenWidth: Int) {
        val bounds = Rect().also { node.getBoundsInScreen(it) }
        val text = node.text?.toString()
        val desc = node.contentDescription?.toString()
        if (text != null || desc != null || node.viewIdResourceName != null || node.isEditable) {
            Log.d(
                TAG,
                buildString {
                    append("  ".repeat(depth))
                    append(node.className?.toString()?.substringAfterLast('.'))
                    append(" id=").append(node.viewIdResourceName?.substringAfter(":id/"))
                    append(" bounds=").append(bounds.toShortString())
                    append(" side=").append(side(bounds, screenWidth))
                    if (node.isEditable) append(" EDITABLE")
                    if (text != null) append(" text=\"").append(redact(text)).append('"')
                    if (desc != null) append(" desc=\"").append(redact(desc)).append('"')
                },
            )
        }
        for (i in 0 until node.childCount) {
            node.getChild(i)?.let { walk(it, depth + 1, screenWidth) }
        }
    }

    /** Rough left/right guess, the same signal WhatsAppScreenParser will use later. */
    private fun side(b: Rect, screenWidth: Int): String {
        val left = b.left
        val right = screenWidth - b.right
        return when {
            b.width() > screenWidth * 0.9 -> "full"
            left > right -> "RIGHT(me?)"
            right > left -> "LEFT(them?)"
            else -> "center"
        }
    }

    private fun insertText(root: AccessibilityNodeInfo, text: String) {
        val input = findInput(root)
        if (input == null) {
            Log.w(TAG, "INSERT: no editable input found — open a chat first")
            return
        }
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = input.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        Log.i(TAG, "INSERT: id=${input.viewIdResourceName} ok=$ok")
    }

    /** Prefer WhatsApp's known input id ("entry"), fall back to any editable node. */
    private fun findInput(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        root.findAccessibilityNodeInfosByViewId("$WHATSAPP:id/entry")
            .firstOrNull { it.isEditable }
            ?.let { return it }
        return findFirst(root) { it.isEditable }
    }

    private fun findFirst(
        node: AccessibilityNodeInfo,
        match: (AccessibilityNodeInfo) -> Boolean,
    ): AccessibilityNodeInfo? {
        if (match(node)) return node
        for (i in 0 until node.childCount) {
            val found = node.getChild(i)?.let { findFirst(it, match) }
            if (found != null) return found
        }
        return null
    }

    private fun redact(s: String): String =
        if (REDACT) "<${s.length} chars>" else s.replace("\n", "\\n")

    companion object {
        const val TAG = "A11ySpike"
        const val WHATSAPP = "com.whatsapp"
        private const val DEBOUNCE_MS = 800L
        private const val TEST_TEXT = "test from spike"

        /** Set true before copying logs anywhere (e.g. spikes/notes.md). */
        @Volatile var REDACT = false

        @Volatile var instance: DumpAccessibilityService? = null
        @Volatile var insertArmed = false
    }
}
