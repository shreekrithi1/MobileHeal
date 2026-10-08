package com.mobileheal.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** REST + WebSocket client for the MobileHeal backend (OkHttp, FR-5.1). */
class HealClient(private val baseUrl: String) {
    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)   // keep the duplex channel alive
        .retryOnConnectionFailure(true)
        .build()
    private val json = "application/json".toMediaType()

    suspend fun getProfile(id: Int): JSONObject? = withContext(Dispatchers.IO) {
        http.newCall(Request.Builder().url("$baseUrl/api/profiles/$id").build()).execute().use {
            if (it.code == 404) null else JSONObject(it.bodyOrThrow())
        }
    }

    suspend fun createProfile(fields: Map<String, String>): JSONObject = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$baseUrl/api/profiles")
            .post(JSONObject(fields).toString().toRequestBody(json)).build()
        http.newCall(req).execute().use { JSONObject(it.bodyOrThrow()) }
    }

    /** Saves fields atomically; server re-evaluates and returns remaining `missing` list (FR-5.4). */
    suspend fun saveProfile(id: Int, fields: Map<String, String>): JSONObject = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$baseUrl/api/profiles/$id")
            .put(JSONObject(fields).toString().toRequestBody(json)).build()
        http.newCall(req).execute().use { JSONObject(it.bodyOrThrow()) }
    }

    fun openNotifications(profileId: Int, listener: WebSocketListener): WebSocket {
        val wsUrl = baseUrl.replaceFirst("http", "ws") + "/ws/notifications?profile_id=$profileId"
        return http.newWebSocket(Request.Builder().url(wsUrl).build(), listener)
    }

    private fun Response.bodyOrThrow(): String {
        val b = body?.string().orEmpty()
        if (!isSuccessful) throw IllegalStateException("HTTP $code: $b")
        return b
    }
}

fun JSONArray.toStringList(): List<String> = (0 until length()).map { getString(it) }
