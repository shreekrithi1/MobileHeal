package com.mobileheal.app

import android.app.Application
import com.mobileheal.app.platform.CrashReporter
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class MobileHealApp : Application() {
    override fun onCreate() {
        super.onCreate()
        CrashReporter.install(this, BuildConfig.BASE_URL)  // production crashes → MobileHeal auto-heal
    }
}
