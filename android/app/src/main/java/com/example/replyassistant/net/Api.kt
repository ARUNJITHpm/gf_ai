package com.example.replyassistant.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

/** The backend API (see backend /docs). Every call except health() needs X-API-Key. */
interface BackendApi {
    @GET("health")
    suspend fun health(): Health

    @GET("auth/check")
    suspend fun authCheck(): AuthCheck

    @POST("ingest")
    suspend fun ingest(@Body body: IngestRequest): IngestResponse

    @POST("suggest")
    suspend fun suggest(@Body body: SuggestRequest): SuggestResponse

    @POST("suggestions/{id}/feedback")
    suspend fun feedback(@Path("id") id: Int, @Body body: FeedbackRequest): FeedbackResponse

    @GET("reminders/due")
    suspend fun remindersDue(@Query("within_hours") withinHours: Int): List<ReminderOut>
}

object ApiFactory {
    val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    fun create(baseUrl: String, apiKey: String, timeoutSeconds: Long = 20): BackendApi {
        val client = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .addInterceptor { chain ->
                chain.proceed(chain.request().newBuilder().header("X-API-Key", apiKey).build())
            }
            .build()
        return Retrofit.Builder()
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(BackendApi::class.java)
    }
}

@Serializable data class Health(val status: String, val db: String, val version: String)

@Serializable data class AuthCheck(val ok: Boolean)

@Serializable
data class IngestMessage(
    val sender: String, // "me" | "contact"
    val text: String,
    @SerialName("sent_at") val sentAt: String? = null, // ISO-8601 with offset
    val source: String, // "notification" | "screen"
)

@Serializable
data class IngestRequest(
    @SerialName("contact_hint") val contactHint: String,
    val messages: List<IngestMessage>,
)

@Serializable
data class IngestResponse(
    @SerialName("contact_id") val contactId: Int,
    val inserted: Int,
    val skipped: Int,
    val updated: Int,
)

@Serializable
data class SuggestRequest(
    @SerialName("contact_hint") val contactHint: String,
    @SerialName("visible_messages") val visibleMessages: List<IngestMessage> = emptyList(),
    @SerialName("draft_text") val draftText: String? = null,
    val tone: String? = null,
)

@Serializable
data class Suggestion(
    val text: String,
    val tone: String,
    val reason: String,
    @SerialName("used_fact_ids") val usedFactIds: List<Int> = emptyList(),
    @SerialName("used_note_ids") val usedNoteIds: List<Int> = emptyList(),
)

@Serializable
data class SuggestResponse(
    @SerialName("suggestion_id") val suggestionId: Int? = null,
    val suggestions: List<Suggestion>,
    val warnings: List<String> = emptyList(),
)

@Serializable data class FeedbackRequest(@SerialName("chosen_index") val chosenIndex: Int)

@Serializable
data class FeedbackResponse(
    @SerialName("suggestion_id") val suggestionId: Int,
    @SerialName("chosen_index") val chosenIndex: Int,
)

@Serializable
data class ReminderOut(
    val id: Int,
    @SerialName("contact_id") val contactId: Int,
    val title: String,
    @SerialName("due_at") val dueAt: String,
    val origin: String,
)
