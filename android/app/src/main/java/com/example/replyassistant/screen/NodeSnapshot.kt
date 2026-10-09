package com.example.replyassistant.screen

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/** Copies the live accessibility tree into plain [ScreenNode]s (bounded in size). */
object NodeSnapshot {
    private const val MAX_DEPTH = 60
    private const val MAX_NODES = 4000

    fun capture(root: AccessibilityNodeInfo): ScreenNode {
        var budget = MAX_NODES
        fun copy(node: AccessibilityNodeInfo, depth: Int): ScreenNode {
            budget--
            val rect = Rect().also { node.getBoundsInScreen(it) }
            val children = if (depth >= MAX_DEPTH) emptyList() else
                (0 until node.childCount).mapNotNull { i ->
                    if (budget <= 0) null else node.getChild(i)?.let { copy(it, depth + 1) }
                }
            return ScreenNode(
                viewId = node.viewIdResourceName,
                className = node.className?.toString(),
                text = node.text?.toString(),
                bounds = Bounds(rect.left, rect.top, rect.right, rect.bottom),
                editable = node.isEditable,
                children = children,
            )
        }
        return copy(root, 0)
    }

    /**
     * JSON for a test fixture. Text is replaced by its length unless [keepText]; never commit
     * a dump with real messages.
     */
    fun toJson(node: ScreenNode, keepText: Boolean = false): JSONObject = JSONObject().apply {
        put("viewId", node.viewId ?: JSONObject.NULL)
        put("className", node.className ?: JSONObject.NULL)
        val text = node.text
        put("text", if (text == null) JSONObject.NULL else if (keepText) text else "<${text.length}ch>")
        put("bounds", JSONArray(listOf(node.bounds.left, node.bounds.top, node.bounds.right, node.bounds.bottom)))
        put("editable", node.editable)
        put("children", JSONArray(node.children.map { toJson(it, keepText) }))
    }
}
