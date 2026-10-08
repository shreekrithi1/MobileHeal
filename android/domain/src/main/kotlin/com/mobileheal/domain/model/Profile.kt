package com.mobileheal.domain.model

/** A user's profile: dynamic attributes keyed by field id (e.g. "name", "phone_number"). */
data class Profile(
    val id: Int,
    val fields: Map<String, String>,
) {
    fun value(field: String): String = fields[field].orEmpty()
}

/** Result of a save: the persisted profile and the required fields the server still considers missing. */
data class SaveOutcome(
    val profile: Profile,
    val missing: List<String>,
)
