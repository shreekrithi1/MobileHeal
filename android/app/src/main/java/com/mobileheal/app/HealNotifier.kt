package com.mobileheal.app

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

class HealNotifier(private val ctx: Context) {
    companion object {
        const val CHANNEL = "auto_heal"
        const val NOTIF_ID = 1001
    }

    init {
        val ch = NotificationChannel(CHANNEL, "Profile updates", NotificationManager.IMPORTANCE_HIGH)
            .apply { description = "Alerts when your profile is missing required information" }
        ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }

    fun showHealRequired(missing: List<String>) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return  // in-app banner still shows
        val open = PendingIntent.getActivity(
            ctx, 0, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val labels = missing.joinToString { it.toLabel() }
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("Complete your profile")
            .setContentText("Please add: $labels")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(ctx).notify(NOTIF_ID, n)
    }

    fun clear() = NotificationManagerCompat.from(ctx).cancel(NOTIF_ID)
}

fun String.toLabel(): String = split('_').joinToString(" ") { it.replaceFirstChar(Char::uppercase) }
