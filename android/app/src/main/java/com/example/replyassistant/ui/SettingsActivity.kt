package com.example.replyassistant.ui

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import com.example.replyassistant.BuildConfig
import com.example.replyassistant.data.Prefs
import com.example.replyassistant.net.ApiFactory
import com.example.replyassistant.notify.WaNotificationListener
import com.example.replyassistant.outbox.OutboxDb
import com.example.replyassistant.screen.ReplyAccessibilityService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class SettingsActivity : ComponentActivity() {
    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        val prefs = Prefs(this)
        // Debug builds only: prefill fields from adb so the long API key needn't be typed:
        //   adb shell am start -n com.example.replyassistant/.ui.SettingsActivity --es api_key KEY --es chat_title "Name"
        // Nothing is saved until Save is tapped. Release builds ignore these extras, since
        // any app can start this activity.
        val prefill = if (BuildConfig.DEBUG) intent?.extras else null
        setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize()) { SettingsScreen(prefs, prefill) }
            }
        }
    }

    @Composable
    private fun SettingsScreen(prefs: Prefs, prefill: Bundle?) {
        var url by remember { mutableStateOf(prefill?.getString("backend_url") ?: prefs.backendUrl) }
        var key by remember { mutableStateOf(prefill?.getString("api_key") ?: prefs.apiKey) }
        var title by remember { mutableStateOf(prefill?.getString("chat_title") ?: prefs.chatTitle) }
        var paused by remember { mutableStateOf(prefs.paused) }
        var result by remember { mutableStateOf(if (prefill?.isEmpty == false) "Prefilled from adb: check and tap Save." else "") }
        var status by remember { mutableStateOf(statusText()) }
        val scope = rememberCoroutineScope()

        // Refresh the permission status when coming back from system settings.
        androidx.compose.runtime.DisposableEffect(Unit) {
            val observer = LifecycleEventObserver { _, event ->
                if (event == Lifecycle.Event.ON_RESUME) status = statusText()
            }
            lifecycle.addObserver(observer)
            onDispose { lifecycle.removeObserver(observer) }
        }

        Column(
            Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("Reply Assistant", style = MaterialTheme.typography.headlineSmall)
            Button(
                { startActivity(Intent(this@SettingsActivity, MemoryActivity::class.java)) },
                Modifier.fillMaxWidth(),
            ) { Text("Memory: notes, facts, rules, dates") }
            Text(
                "Suggests replies in the chat below. It never sends anything: you edit and send.",
                style = MaterialTheme.typography.bodyMedium,
            )
            OutlinedTextField(url, { url = it }, Modifier.fillMaxWidth(), label = { Text("Backend URL") }, singleLine = true)
            OutlinedTextField(
                key, { key = it }, Modifier.fillMaxWidth(),
                label = { Text("API key") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
            )
            OutlinedTextField(
                title, { title = it }, Modifier.fillMaxWidth(),
                label = { Text("Chat title (as WhatsApp shows it)") }, singleLine = true,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Pause assistant", Modifier.weight(1f))
                Switch(paused, { paused = it; prefs.paused = it })
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button({
                    prefs.backendUrl = url; prefs.apiKey = key; prefs.chatTitle = title
                    result = "Saved."
                }) { Text("Save") }
                OutlinedButton({
                    prefs.backendUrl = url; prefs.apiKey = key; prefs.chatTitle = title
                    result = "Testing…"
                    scope.launch { result = testConnection(prefs) }
                }) { Text("Test connection") }
            }
            if (result.isNotEmpty()) Text(result)

            Spacer(Modifier.padding(4.dp))
            Text("Permissions", style = MaterialTheme.typography.titleMedium)
            Text(status)
            OutlinedButton({ startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }, Modifier.fillMaxWidth()) {
                Text("Open Accessibility settings")
            }
            OutlinedButton({ startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }, Modifier.fillMaxWidth()) {
                Text("Open Notification access settings")
            }
            OutlinedButton({
                ReplyAccessibilityService.dumpArmed = true
                result = "Open the chat in WhatsApp; the screen structure is saved (text redacted)."
            }, Modifier.fillMaxWidth()) { Text("Debug: dump next chat screen") }
        }
    }

    private fun statusText(): String {
        val a11y = ReplyAccessibilityService.instance != null
        val listener = ComponentName(this, WaNotificationListener::class.java).flattenToString()
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners")
        val notif = enabled?.split(":")?.contains(listener) == true
        val queued = runCatching {
            kotlinx.coroutines.runBlocking(Dispatchers.IO) { OutboxDb.get(this@SettingsActivity).dao().count() }
        }.getOrDefault(0)
        return "Accessibility service: ${if (a11y) "ON" else "OFF"}\n" +
            "Notification access: ${if (notif) "ON" else "OFF"}\n" +
            "Messages waiting to upload: $queued"
    }

    private suspend fun testConnection(prefs: Prefs): String = withContext(Dispatchers.IO) {
        val api = ApiFactory.create(prefs.backendUrl, prefs.apiKey)
        try {
            val health = api.health()
            api.authCheck()
            "Connected: backend ${health.version}, database ${health.db}, API key OK."
        } catch (e: retrofit2.HttpException) {
            if (e.code() == 401) "Backend reached, but the API key is wrong." else "Backend error ${e.code()}."
        } catch (e: Exception) {
            "Can't reach ${prefs.backendUrl}: ${e.message ?: e.javaClass.simpleName}"
        }
    }
}
