package com.mobileheal.data.remote

import javax.inject.Qualifier

/** Base URL of the MobileHeal backend (provided by :app from BuildConfig). */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class BaseUrl

/** X-MobileHeal-* identification headers (app id, rules version, source folder) sent with every call. */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class ClientHeaders
