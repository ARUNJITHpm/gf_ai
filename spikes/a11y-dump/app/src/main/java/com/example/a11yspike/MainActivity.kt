package com.example.a11yspike

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.TextView

/** Bare-bones control screen for the spike. No Compose, no XML layouts. */
class MainActivity : Activity() {

    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        status = TextView(this)

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            addView(status)
            addView(button("Open Accessibility settings") {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            })
            addView(button("Open Notification access settings") {
                startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            })
            addView(button("Arm insert test (then open a WhatsApp chat)") {
                DumpAccessibilityService.insertArmed = true
                refresh()
            })
            addView(CheckBox(context).apply {
                text = "Redact message text in logs"
                isChecked = DumpAccessibilityService.REDACT
                setOnCheckedChangeListener { _, checked -> DumpAccessibilityService.REDACT = checked }
            })
        }
        setContentView(layout)
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun refresh() {
        val a11y = DumpAccessibilityService.instance != null
        status.text = buildString {
            appendLine("Accessibility service: ${if (a11y) "ON" else "OFF"}")
            appendLine("Insert armed: ${DumpAccessibilityService.insertArmed}")
            appendLine()
            appendLine("Watch logs on the PC:")
            appendLine("adb logcat -s A11ySpike A11ySpikeNotif")
        }
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        setOnClickListener { onClick() }
    }
}
