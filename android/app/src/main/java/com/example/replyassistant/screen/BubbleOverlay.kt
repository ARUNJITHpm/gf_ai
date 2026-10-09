package com.example.replyassistant.screen

import android.annotation.SuppressLint
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.example.replyassistant.data.Prefs
import com.example.replyassistant.net.SuggestResponse
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlin.math.abs

/**
 * The floating bubble (shown only while the configured chat is open) and the suggestion
 * panel it opens. Plain Views on a TYPE_ACCESSIBILITY_OVERLAY window: no extra permission
 * needed, and nothing here can reach WhatsApp's Send button.
 */
class BubbleOverlay(private val service: ReplyAccessibilityService) {
    private val wm = service.getSystemService(WindowManager::class.java)
    private val density = service.resources.displayMetrics.density
    private val prefs = Prefs(service)
    private val scope = CoroutineScope(Dispatchers.Main)
    private var bubble: View? = null
    private var bubbleParams: WindowManager.LayoutParams? = null
    private var panel: View? = null
    private var loadJob: Job? = null

    fun show() {
        if (bubble != null) return
        val view = TextView(service).apply {
            text = "💬"
            textSize = 22f
            gravity = Gravity.CENTER
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.parseColor("#1E7A5A"))
            }
            elevation = 8 * density
            contentDescription = "Reply suggestions"
        }
        val size = dp(52)
        val params = overlayParams(size, size).apply {
            gravity = Gravity.TOP or Gravity.END
            x = dp(8)
            y = dp(180)
        }
        enableDragAndTap(view, params)
        wm.addView(view, params)
        bubble = view
        bubbleParams = params
    }

    fun hide() {
        closePanel()
        bubble?.let { runCatching { wm.removeView(it) } }
        bubble = null
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun enableDragAndTap(view: View, params: WindowManager.LayoutParams) {
        var startX = 0
        var startY = 0
        var touchX = 0f
        var touchY = 0f
        view.setOnTouchListener { v, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    startX = params.x; startY = params.y
                    touchX = event.rawX; touchY = event.rawY
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    params.x = startX - (event.rawX - touchX).toInt() // gravity END: x grows leftwards
                    params.y = startY + (event.rawY - touchY).toInt()
                    runCatching { wm.updateViewLayout(v, params) }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    val moved = abs(event.rawX - touchX) + abs(event.rawY - touchY)
                    if (moved < 12 * density) togglePanel()
                    true
                }
                else -> false
            }
        }
    }

    private fun togglePanel() {
        if (panel != null) closePanel() else openPanel()
    }

    private fun closePanel() {
        loadJob?.cancel()
        panel?.let { runCatching { wm.removeView(it) } }
        panel = null
    }

    private fun openPanel() {
        val content = LinearLayout(service).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        val status = TextView(service).apply { textSize = 13f; setTextColor(Color.DKGRAY) }
        val list = LinearLayout(service).apply { orientation = LinearLayout.VERTICAL }

        val header = LinearLayout(service).apply { orientation = LinearLayout.HORIZONTAL }
        header.addView(TextView(service).apply {
            text = "Suggestions"
            textSize = 16f
            setTypeface(typeface, Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
        header.addView(smallButton("↻") { load(status, list) })
        header.addView(smallButton("✕") { closePanel() })
        content.addView(header)

        val tones = LinearLayout(service).apply { orientation = LinearLayout.HORIZONTAL }
        for ((label, value) in TONES) {
            tones.addView(smallButton(if (prefs.tone == value) "• $label" else label) {
                prefs.tone = value
                closePanel()
                openPanel()
            })
        }
        content.addView(HorizontalScrollView(service).apply { addView(tones) })
        content.addView(status)
        content.addView(list)

        val frame = ScrollView(service).apply {
            addView(content)
            background = GradientDrawable().apply {
                cornerRadius = 16 * density
                setColor(Color.WHITE)
                setStroke(dp(1), Color.parseColor("#DDDDDD"))
            }
            elevation = 12 * density
        }
        val width = (service.resources.displayMetrics.widthPixels * 0.92).toInt()
        val params = overlayParams(width, WindowManager.LayoutParams.WRAP_CONTENT).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            y = dp(90)
        }
        wm.addView(frame, params)
        panel = frame
        load(status, list)
    }

    private fun load(status: TextView, list: LinearLayout) {
        loadJob?.cancel()
        list.removeAllViews()
        status.text = "Thinking…"
        loadJob = scope.launch {
            val result = service.fetchSuggestions(prefs.tone)
            result.onSuccess { render(it, status, list) }
                .onFailure { status.text = "Couldn't get suggestions: ${it.message ?: it.javaClass.simpleName}" }
        }
    }

    private fun render(response: SuggestResponse, status: TextView, list: LinearLayout) {
        status.text = response.warnings.joinToString("\n").ifBlank {
            if (response.suggestions.isEmpty()) "No suggestions." else "Tap one to put it in the message box. Long-press for why."
        }
        response.suggestions.forEachIndexed { index, suggestion ->
            list.addView(Button(service).apply {
                text = suggestion.text
                isAllCaps = false
                textSize = 15f
                gravity = Gravity.START or Gravity.CENTER_VERTICAL
                setOnClickListener {
                    val ok = service.insertText(suggestion.text, response.suggestionId, index)
                    if (ok) closePanel() else status.text = "Couldn't find the message box."
                }
                setOnLongClickListener {
                    status.text = "${suggestion.tone}: ${suggestion.reason}"
                    true
                }
            })
        }
    }

    private fun smallButton(label: String, onClick: () -> Unit) = Button(service).apply {
        text = label
        isAllCaps = false
        textSize = 13f
        minWidth = 0
        minimumWidth = 0
        setPadding(dp(8), 0, dp(8), 0)
        setOnClickListener { onClick() }
    }

    private fun overlayParams(width: Int, height: Int) = WindowManager.LayoutParams(
        width,
        height,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    )

    private fun dp(value: Int) = (value * density).toInt()

    private companion object {
        val TONES = listOf("Auto" to "", "Warm" to "warm", "Playful" to "playful", "Caring" to "caring", "Sorry" to "apologetic")
    }
}
