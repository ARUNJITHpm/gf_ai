package com.example.replyassistant.reminders

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.example.replyassistant.R
import com.example.replyassistant.data.Prefs
import com.example.replyassistant.net.ApiFactory
import java.util.concurrent.TimeUnit

/** Every 3 hours: fetch reminders due soon and show each one once as a notification. */
class ReminderWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val prefs = Prefs(applicationContext)
        if (!prefs.isConfigured || prefs.paused) return Result.success()
        val due = try {
            ApiFactory.create(prefs.backendUrl, prefs.apiKey).remindersDue(withinHours = 3)
        } catch (e: Exception) {
            return Result.retry()
        }
        val shown = prefs.shownReminderIds.toMutableSet()
        for (reminder in due) {
            if (reminder.id.toString() in shown) continue
            notify(reminder.id, reminder.title)
            shown += reminder.id.toString()
        }
        // Keep the set small: only ids the backend still reports as open.
        prefs.shownReminderIds = shown.filter { id -> due.any { it.id.toString() == id } }.toSet() +
            due.map { it.id.toString() }
        return Result.success()
    }

    private fun notify(id: Int, title: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(R.drawable.ic_bubble)
            .setContentTitle("Reminder")
            .setContentText(title)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(applicationContext).notify(NOTIFY_BASE + id, notification)
    }

    companion object {
        private const val CHANNEL = "reminders"
        private const val NOTIFY_BASE = 10_000

        fun createChannel(context: Context) {
            val channel = NotificationChannel(CHANNEL, "Reminders", NotificationManager.IMPORTANCE_DEFAULT)
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }

        fun schedule(context: Context) {
            val work = PeriodicWorkRequestBuilder<ReminderWorker>(3, TimeUnit.HOURS)
                .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
                .build()
            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork("reminders", ExistingPeriodicWorkPolicy.KEEP, work)
        }
    }
}
