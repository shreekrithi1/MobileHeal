package com.mobileheal.data.remote

import com.mobileheal.data.mapper.toProfileDto
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import javax.inject.Inject

/** Thin REST client. Returns DTOs; throws [HttpStatusException] / IOException for the repository to map. */
class ProfileApi @Inject constructor(
    private val client: OkHttpClient,
    @BaseUrl private val baseUrl: String,
) {
    private val json = "application/json".toMediaType()

    suspend fun get(id: Int): ProfileDto = call(Request.Builder().url("$baseUrl/api/profiles/$id").build())

    suspend fun create(fields: Map<String, String>): ProfileDto =
        call(Request.Builder().url("$baseUrl/api/profiles").post(JSONObject(fields).toString().toRequestBody(json)).build())

    suspend fun update(id: Int, fields: Map<String, String>): ProfileDto =
        call(Request.Builder().url("$baseUrl/api/profiles/$id").put(JSONObject(fields).toString().toRequestBody(json)).build())

    private suspend fun call(request: Request): ProfileDto = withContext(Dispatchers.IO) {
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw HttpStatusException(response.code, body)
            JSONObject(body).toProfileDto()
        }
    }
}
