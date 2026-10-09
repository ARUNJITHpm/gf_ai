package com.example.replyassistant.outbox

import android.content.Context
import android.util.Log
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.example.replyassistant.data.Prefs
import com.example.replyassistant.net.ApiFactory
import com.example.replyassistant.net.IngestRequest
import retrofit2.HttpException
import java.util.concurrent.TimeUnit

/**
 * Every ingest payload goes through this queue: stored in Room first, then delivered by a
 * WorkManager job. If the backend is down (PC asleep, no Tailscale), payloads wait and are
 * retried with backoff. Message text stays on the phone until delivered.
 */
@Entity(tableName = "outbox")
data class OutboxItem(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val payload: String,
    val createdAt: Long = System.currentTimeMillis(),
)

@Dao
interface OutboxDao {
    @Insert suspend fun insert(item: OutboxItem): Long

    @Query("SELECT * FROM outbox ORDER BY id LIMIT :limit")
    suspend fun oldest(limit: Int): List<OutboxItem>

    @Query("DELETE FROM outbox WHERE id = :id")
    suspend fun delete(id: Long)

    @Query("SELECT COUNT(*) FROM outbox")
    suspend fun count(): Int
}

@Database(entities = [OutboxItem::class], version = 1, exportSchema = false)
abstract class OutboxDb : RoomDatabase() {
    abstract fun dao(): OutboxDao

    companion object {
        @Volatile private var instance: OutboxDb? = null

        fun get(context: Context): OutboxDb = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(context.applicationContext, OutboxDb::class.java, "outbox.db")
                .build()
                .also { instance = it }
        }
    }
}

object Outbox {
    private const val WORK_NAME = "outbox-drain"

    suspend fun enqueue(context: Context, request: IngestRequest) {
        val payload = ApiFactory.json.encodeToString(IngestRequest.serializer(), request)
        OutboxDb.get(context).dao().insert(OutboxItem(payload = payload))
        scheduleDrain(context)
    }

    fun scheduleDrain(context: Context) {
        val work = OneTimeWorkRequestBuilder<OutboxWorker>()
            .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        // APPEND_OR_REPLACE: a drain already queued or running still picks up new items.
        WorkManager.getInstance(context)
            .enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.APPEND_OR_REPLACE, work)
    }
}

class OutboxWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val prefs = Prefs(applicationContext)
        if (prefs.apiKey.isBlank()) return Result.retry()
        val api = ApiFactory.create(prefs.backendUrl, prefs.apiKey)
        val dao = OutboxDb.get(applicationContext).dao()
        while (true) {
            val batch = dao.oldest(20)
            if (batch.isEmpty()) return Result.success()
            for (item in batch) {
                try {
                    val request = ApiFactory.json.decodeFromString(IngestRequest.serializer(), item.payload)
                    api.ingest(request)
                    dao.delete(item.id)
                } catch (e: HttpException) {
                    if (e.code() in 400..499 && e.code() != 401 && e.code() != 429) {
                        // Unknown contact (404) or invalid payload (422): retrying won't help.
                        Log.w(TAG, "dropping outbox item ${item.id}: HTTP ${e.code()}")
                        dao.delete(item.id)
                    } else {
                        return Result.retry() // 401 (key not set yet), 429, 5xx
                    }
                } catch (e: Exception) {
                    return Result.retry() // network down, backend asleep
                }
            }
        }
    }

    private companion object {
        const val TAG = "ReplyOutbox"
    }
}
