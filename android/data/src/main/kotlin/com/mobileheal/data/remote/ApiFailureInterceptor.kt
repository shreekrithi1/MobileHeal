package com.mobileheal.data.remote

import android.os.Build
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Tags every API call as coming from the Android app and, when the backend answers with HTTP 5xx,
 * reports the failure to MobileHeal (`POST /api/client-errors`). The report — not the server — starts
 * the auto-heal: MobileHeal links it to the server-side incident (key from the 500 body) and opens the defect.
 */
class ApiFailureInterceptor(private val baseUrl: String) : Interceptor {
    private val reporter = OkHttpClient()
    private val json = "application/json".toMediaType()

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request().newBuilder().header("X-MobileHeal-Client", "android").build()
        val response = chain.proceed(request)
        val path = request.url.encodedPath
        if (response.code >= 500 && path != REPORT_PATH) {
            val body = runCatching { response.peekBody(2_000).string() }.getOrDefault("")
            report(request, response.code, body)
        }
        return response
    }

    private fun report(request: Request, code: Int, body: String) = thread(name = "mh-api-failure") {
        val incident = runCatching { JSONObject(body).optString("incident", "") }.getOrDefault("")
        val payload = JSONObject()
            .put("platform", "android")
            .put("method", request.method)
            .put("endpoint", request.url.encodedPath + (request.url.encodedQuery?.let { "?$it" } ?: ""))
            .put("status", code)
            .put("incident", incident.ifEmpty { JSONObject.NULL })
            .put("body", body.take(2_000))
            .put("device", "${Build.MANUFACTURER} ${Build.MODEL} (API ${Build.VERSION.SDK_INT})")
            .put("screen", "Profile")
        runCatching {
            reporter.newCall(
                Request.Builder().url("$baseUrl$REPORT_PATH").header("X-MobileHeal-Client", "android")
                    .post(payload.toString().toRequestBody(json)).build(),
            ).execute().close()
        }
    }

    private companion object {
        const val REPORT_PATH = "/api/client-errors"
    }
}
