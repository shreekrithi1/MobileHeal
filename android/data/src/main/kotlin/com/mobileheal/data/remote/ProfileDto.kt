package com.mobileheal.data.remote

/** Network model for /api/profiles. Standard columns are flattened into [fields] alongside dynamic attributes. */
data class ProfileDto(
    val id: Int,
    val fields: Map<String, String>,
    val missing: List<String>,
)

class HttpStatusException(val code: Int, val body: String) : RuntimeException("HTTP $code")
