package com.mobileheal.data.mapper

import com.mobileheal.data.remote.ProfileDto
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.FieldRule
import com.mobileheal.domain.model.LiveEvent
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.model.SaveOutcome
import org.json.JSONArray
import org.json.JSONObject

private val META_KEYS = setOf("id", "updated_at", "missing")

fun JSONObject.toProfileDto(): ProfileDto {
    val fields = buildMap {
        keys().forEach { key ->
            if (key !in META_KEYS && !isNull(key)) put(key, get(key).toString())
        }
    }
    return ProfileDto(id = getInt("id"), fields = fields, missing = optJSONArray("missing")?.toStringList().orEmpty())
}

fun ProfileDto.toDomain(): Profile = Profile(id = id, fields = fields)

fun ProfileDto.toSaveOutcome(): SaveOutcome = SaveOutcome(toDomain(), missing)

/** Maps one WebSocket message; returns null for message types this app doesn't handle. */
fun JSONObject.toLiveEvent(): LiveEvent? = when (optString("type")) {
    "HEAL_REQUIRED" -> LiveEvent.HealRequired(optJSONArray("missing")?.toStringList().orEmpty())
    "HEAL_RESOLVED" -> LiveEvent.HealResolved
    "CONFIG_UPDATED" -> LiveEvent.RulesUpdated(toAppRules())
    else -> null
}

fun JSONObject.toAppRules(): AppRules {
    val rulesJson = optJSONArray("rules") ?: JSONArray()
    val fields = (0 until rulesJson.length()).map { i ->
        val r = rulesJson.getJSONObject(i)
        FieldRule(r.getString("field"), r.optString("constraint") == "required")
    }
    val uiJson = optJSONObject("ui") ?: JSONObject()
    val ui = buildMap { uiJson.keys().forEach { k -> put(k, uiJson.getString(k)) } }
    return AppRules(fields, ui)
}

fun JSONArray.toStringList(): List<String> = (0 until length()).map { getString(it) }
