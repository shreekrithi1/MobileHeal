package com.mobileheal.app

import android.app.Application
import com.google.firebase.FirebaseApp
import com.google.firebase.analytics.FirebaseAnalytics
import com.mobileheal.app.platform.CrashReporter
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class MobileHealApp : Application() {
    override fun onCreate() {
        super.onCreate()
        runCatching {
            FirebaseApp.initializeApp(this)
            FirebaseAnalytics.getInstance(this).logEvent("app_open", null)
        }
        CrashReporter.install(this, BuildConfig.BASE_URL)
        if (BuildConfig.DEBUG) {
            runCatching {
                CrashReporter.report(
                    BuildConfig.BASE_URL,
                    RuntimeException("MobileHeal Crashlytics Integration Verified")
                )
            }
        }
    }
}
