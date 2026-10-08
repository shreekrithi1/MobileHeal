package com.mobileheal.data.repository

import com.mobileheal.data.mapper.toLiveEvent
import com.mobileheal.data.remote.BaseUrl
import com.mobileheal.domain.model.LiveEvent
import com.mobileheal.domain.repository.LiveUpdatesRepository
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.retryWhen
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import javax.inject.Inject

/** WebSocket stream of backend events with automatic reconnect. */
class LiveUpdatesRepositoryImpl @Inject constructor(
    private val client: OkHttpClient,
    @BaseUrl private val baseUrl: String,
) : LiveUpdatesRepository {

    override fun events(profileId: Int): Flow<LiveEvent> = callbackFlow {
        val url = baseUrl.replaceFirst("http", "ws") + "/ws/notifications?profile_id=$profileId"
        val socket = client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                trySend(LiveEvent.ConnectionChanged(true))
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching { JSONObject(text).toLiveEvent() }.getOrNull()?.let { trySend(it) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                trySend(LiveEvent.ConnectionChanged(false))
                close(t)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                trySend(LiveEvent.ConnectionChanged(false))
                close()
            }
        })
        awaitClose { socket.cancel() }
    }.retryWhen { _, _ ->
        delay(RECONNECT_DELAY_MS)
        true
    }

    private companion object {
        const val RECONNECT_DELAY_MS = 3_000L
    }
}
