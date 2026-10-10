package com.mobileheal.app.platform

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MobileHealMessagingService : FirebaseMessagingService() {

    @Inject
    lateinit var healNotifier: HealNotifier

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.d(TAG, "New FCM token received: $token")
    }

    override fun onMessageReceived(remoteMessage: RemoteMessage) {
        super.onMessageReceived(remoteMessage)
        Log.d(TAG, "FCM message received from: ${remoteMessage.from}")

        // Handle notification payload
        remoteMessage.notification?.let { notification ->
            Log.d(TAG, "Notification title: ${notification.title}, body: ${notification.body}")
        }

        // Handle data payload for profile missing fields / heal alerts
        if (remoteMessage.data.isNotEmpty()) {
            val missingFieldsStr = remoteMessage.data["missing"]
            if (!missingFieldsStr.isNullOrEmpty()) {
                val missingFields = missingFieldsStr.split(",").map { it.trim() }
                healNotifier.show(missingFields, remoteMessage.data)
            }
        }
    }

    companion object {
        private const val TAG = "MobileHealFCM"
    }
}
