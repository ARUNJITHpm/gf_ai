package com.example.replyassistant

import android.app.Application
import com.example.replyassistant.outbox.Outbox
import com.example.replyassistant.reminders.ReminderWorker

class ReplyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        ReminderWorker.createChannel(this)
        ReminderWorker.schedule(this)
        Outbox.scheduleDrain(this) // deliver anything queued before the app was last stopped
    }
}
