package com.example.replyassistant.ui

import android.app.DatePickerDialog
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.replyassistant.data.Prefs
import com.example.replyassistant.net.ApiFactory
import com.example.replyassistant.net.BackendApi
import com.example.replyassistant.net.FactIn
import com.example.replyassistant.net.FactOut
import com.example.replyassistant.net.FactPatch
import com.example.replyassistant.net.NoteIn
import com.example.replyassistant.net.NoteOut
import kotlinx.coroutines.launch
import java.time.LocalDate

/**
 * What the assistant remembers about her and about us: write notes and facts, review what
 * the extractor proposed. Everything is stored on the backend, for the configured chat.
 */
class MemoryActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize()) {
                    if (!prefs.isConfigured) {
                        Text(
                            "Set the backend, API key and chat title in Settings first.",
                            Modifier.padding(16.dp),
                        )
                    } else {
                        MemoryScreen(ApiFactory.create(prefs.backendUrl, prefs.apiKey), prefs.chatTitle)
                    }
                }
            }
        }
    }
}

/** The kinds of things I can save, and where each one goes on the backend. */
private enum class Kind(val label: String, val category: String?, val help: String) {
    NOTE("Note", null, "A thought or something worth knowing. Used when the chat is about it. Write as many as you like."),
    FACT("Fact", "profile", "Always remembered, e.g. \"She's vegetarian\". Keep these few."),
    SITUATION("Situation", "context", "What's going on now, e.g. \"We're long distance until March\". Always remembered."),
    RULE("Rule", "rule", "Never broken, e.g. \"Never joke about her weight\"."),
    DATE("Date", "date", "Birthdays, anniversaries. You get reminders 7 days and 1 day before."),
    JOKE("Inside joke", "inside_joke", "Something only we get. Always remembered."),
}

@Composable
private fun MemoryScreen(api: BackendApi, chat: String) {
    var tab by remember { mutableIntStateOf(0) }
    var reviewCount by remember { mutableIntStateOf(0) }
    Column(Modifier.fillMaxSize()) {
        Text(
            "Memory: $chat",
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.padding(16.dp, 16.dp, 16.dp, 8.dp),
        )
        PrimaryTabRow(selectedTabIndex = tab) {
            Tab(tab == 0, { tab = 0 }, text = { Text("Add") })
            Tab(tab == 1, { tab = 1 }, text = { Text("Saved") })
            Tab(tab == 2, { tab = 2 }, text = { Text(if (reviewCount > 0) "Review ($reviewCount)" else "Review") })
        }
        LaunchedEffect(Unit) {
            reviewCount = runCatching { api.notes(chat, "pending").size }.getOrDefault(0)
        }
        when (tab) {
            0 -> AddTab(api, chat)
            1 -> SavedTab(api, chat)
            else -> ReviewTab(api, chat) { reviewCount = it }
        }
    }
}

@Composable
private fun AddTab(api: BackendApi, chat: String) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var kind by remember { mutableStateOf(Kind.NOTE) }
    var text by remember { mutableStateOf("") }
    var date by remember { mutableStateOf<LocalDate?>(null) }
    var yearly by remember { mutableStateOf(true) }
    var status by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }

    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (k in Kind.entries) FilterChip(kind == k, { kind = k }, label = { Text(k.label) })
        }
        Text(kind.help, style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(
            text,
            { text = it },
            Modifier.fillMaxWidth().heightIn(min = 120.dp),
            label = { Text(if (kind == Kind.DATE) "What is it? (e.g. Her birthday)" else "Write it down") },
        )
        if (kind == Kind.DATE) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton({
                    val start = date ?: LocalDate.now()
                    DatePickerDialog(context, { _, y, m, d -> date = LocalDate.of(y, m + 1, d) },
                        start.year, start.monthValue - 1, start.dayOfMonth).show()
                }) { Text(date?.toString() ?: "Pick date") }
                Checkbox(yearly, { yearly = it })
                Text("Every year")
            }
        }
        Button(
            enabled = !saving && text.isNotBlank() && (kind != Kind.DATE || date != null),
            onClick = {
                saving = true
                scope.launch {
                    status = runCatching {
                        if (kind == Kind.NOTE) {
                            api.createNote(NoteIn(chat, text.trim()))
                        } else {
                            api.createFact(
                                FactIn(
                                    contactHint = chat,
                                    category = kind.category!!,
                                    content = text.trim(),
                                    eventDate = date?.takeIf { kind == Kind.DATE }?.toString(),
                                    recurrence = if (kind == Kind.DATE && yearly) "yearly" else "none",
                                ),
                            )
                        }
                    }.fold(
                        onSuccess = { text = ""; date = null; "Saved as ${kind.label.lowercase()}." },
                        onFailure = { "Couldn't save: ${it.message ?: it.javaClass.simpleName}" },
                    )
                    saving = false
                }
            },
        ) { Text(if (saving) "Saving…" else "Save") }
        if (status.isNotEmpty()) Text(status)
    }
}

@Composable
private fun SavedTab(api: BackendApi, chat: String) {
    val scope = rememberCoroutineScope()
    var facts by remember { mutableStateOf<List<FactOut>>(emptyList()) }
    var notes by remember { mutableStateOf<List<NoteOut>>(emptyList()) }
    var status by remember { mutableStateOf("Loading…") }
    var confirm by remember { mutableStateOf<Pair<String, suspend () -> Unit>?>(null) }

    suspend fun reload() {
        status = runCatching {
            facts = api.facts(chat)
            notes = api.notes(chat, "approved")
            if (facts.isEmpty() && notes.isEmpty()) "Nothing saved yet." else ""
        }.getOrElse { "Couldn't load: ${it.message ?: it.javaClass.simpleName}" }
    }
    LaunchedEffect(Unit) { reload() }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (status.isNotEmpty()) item { Text(status, Modifier.padding(top = 12.dp)) }
        if (facts.isNotEmpty()) item { Text("Always remembered", style = MaterialTheme.typography.titleMedium) }
        items(facts, key = { "f${it.id}" }) { fact ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    val label = Kind.entries.firstOrNull { it.category == fact.category }?.label ?: fact.category
                    val dateText = fact.eventDate?.let { " · $it" + if (fact.recurrence == "yearly") " (every year)" else "" } ?: ""
                    Text("$label$dateText", style = MaterialTheme.typography.labelMedium)
                    Text(fact.content)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("In use", Modifier.weight(1f))
                        Switch(fact.active, { on ->
                            scope.launch {
                                runCatching { api.updateFact(fact.id, FactPatch(active = on)) }
                                reload()
                            }
                        })
                        TextButton({
                            confirm = "Delete this ${label.lowercase()}?" to { api.deleteFact(fact.id); reload() }
                        }) { Text("Delete") }
                    }
                }
            }
        }
        if (notes.isNotEmpty()) item { Text("Notes", style = MaterialTheme.typography.titleMedium) }
        items(notes, key = { "n${it.id}" }) { note ->
            Card(Modifier.fillMaxWidth()) {
                Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(note.content, Modifier.weight(1f))
                    TextButton({ confirm = "Delete this note?" to { api.deleteNote(note.id); reload() } }) {
                        Text("Delete")
                    }
                }
            }
        }
    }

    confirm?.let { (question, action) ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text(question) },
            confirmButton = {
                TextButton({
                    confirm = null
                    scope.launch { runCatching { action() }.onFailure { status = "Couldn't delete: ${it.message}" } }
                }) { Text("Delete") }
            },
            dismissButton = { TextButton({ confirm = null }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun ReviewTab(api: BackendApi, chat: String, onCount: (Int) -> Unit) {
    val scope = rememberCoroutineScope()
    var pending by remember { mutableStateOf<List<NoteOut>>(emptyList()) }
    var status by remember { mutableStateOf("Loading…") }

    suspend fun reload() {
        status = runCatching {
            pending = api.notes(chat, "pending")
            onCount(pending.size)
            if (pending.isEmpty()) "Nothing to review. New proposals appear here as you chat." else ""
        }.getOrElse { "Couldn't load: ${it.message ?: it.javaClass.simpleName}" }
    }
    LaunchedEffect(Unit) { reload() }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            Text(
                "The assistant proposed these from your chats. Approve to let it use them.",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(top = 12.dp),
            )
        }
        if (status.isNotEmpty()) item { Text(status) }
        items(pending, key = { it.id }) { note ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    Text(note.content)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button({ scope.launch { runCatching { api.approveNote(note.id) }; reload() } }) { Text("Approve") }
                        OutlinedButton({ scope.launch { runCatching { api.rejectNote(note.id) }; reload() } }) { Text("Reject") }
                    }
                }
            }
        }
    }
}
