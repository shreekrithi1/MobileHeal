package com.mobileheal.data.remote

import javax.inject.Qualifier

/** Base URL of the MobileHeal backend (provided by :app from BuildConfig). */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class BaseUrl
