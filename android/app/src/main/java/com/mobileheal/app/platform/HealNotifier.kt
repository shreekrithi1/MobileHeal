package com.mobileheal.app.platform

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.mobileheal.app.MainActivity
import com.mobileheal.app.ui.toLabel
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/** Abstraction so the ViewModel stays unit-testable. */
interface HealAlerts {
    fun show(missing: List<String>, reasons: Map<String, String> = emptyMap())
    fun clear()
}

@Singleton
class HealNotifier @Inject constructor(@ApplicationContext private val context: Context) : HealAlerts {
    init {
        val channel = NotificationChannel(CHANNEL, "Profile updates", NotificationManager.IMPORTANCE_HIGH)
            .apply { description = "Alerts when your profile is missing required information" }
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    override fun show(missing: List<String>, reasons: Map<String, String>) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return  // the in-app banner still shows
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("Complete your profile")
            .setContentText("Please add: ${missing.joinToString { it.toLabel() }}")
            .apply {
                if (reasons.isNotEmpty()) {
                    setContentTitle("Action needed on your profile")
                    setStyle(NotificationCompat.BigTextStyle().bigText(missing.joinToString("\n") { "• " + (reasons[it] ?: "Add your ${it.toLabel()}") }))
                }
            }
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, notification)
    }

    override fun clear() = NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID)

    private companion object {
        const val CHANNEL = "auto_heal"
        const val NOTIFICATION_ID = 1001
    }
}
