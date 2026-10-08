package com.mobileheal.app

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import com.mobileheal.app.generated.RulesDefaults

data class UiState(
    val profileId: Int = BuildConfig.PROFILE_ID,
    // name & email always shown; generated defaults add the fields required at build time
    val fields: Map<String, String> =
        (listOf("name", "email") + RulesDefaults.REQUIRED_FIELDS).distinct().associateWith { "" },
    val missing: List<String> = emptyList(),
    val connected: Boolean = false,
    val saving: Boolean = false,
    val message: String? = null,
    val ui: Map<String, String> = RulesDefaults.UI,   // business/UI rules pushed from the dashboard
)

class ProfileViewModel(app: Application) : AndroidViewModel(app) {
    private val client = HealClient(BuildConfig.BASE_URL)
    private val notifier = HealNotifier(app)
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state
    private var socket: WebSocket? = null

    init {
        load()
        connect()
    }

    private fun load() = viewModelScope.launch {
        try {
            val p = client.getProfile(_state.value.profileId)
                ?: client.createProfile(mapOf("name" to "Jane Doe", "email" to "jane@example.com"))
            applyProfile(p)
        } catch (e: Exception) {
            _state.update { it.copy(message = "Cannot reach backend: ${e.message}") }
        }
    }

    private fun applyProfile(p: JSONObject) {
        val id = p.getInt("id")
        _state.update { s ->
            val f = s.fields.toMutableMap()
            f.keys.toList().forEach { k -> f[k] = p.optString(k, "").takeUnless { p.isNull(k) } ?: "" }
            s.copy(profileId = id, fields = f)
        }
    }

    /** Persistent WebSocket with auto-reconnect. */
    private fun connect() {
        socket = client.openNotifications(_state.value.profileId, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                _state.update { it.copy(connected = true) }
            }
            override fun onMessage(webSocket: WebSocket, text: String) = handleEvent(JSONObject(text))
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = reconnect()
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = reconnect()
        })
    }

    private fun reconnect() {
        _state.update { it.copy(connected = false) }
        viewModelScope.launch { delay(3000); connect() }
    }

    private fun handleEvent(evt: JSONObject) {
        when (evt.optString("type")) {
            "HEAL_REQUIRED" -> {
                val missing = evt.getJSONArray("missing").toStringList()
                _state.update { s ->
                    // add an input for each newly-required dynamic field
                    val f = s.fields.toMutableMap().apply { missing.forEach { putIfAbsent(it, "") } }
                    s.copy(fields = f, missing = missing)
                }
                notifier.showHealRequired(missing)   // FR-5.3 system notification
            }
            "CONFIG_UPDATED" -> {
                val uiObj = evt.optJSONObject("ui") ?: JSONObject()
                val ui = uiObj.keys().asSequence().associateWith { uiObj.getString(it) }
                val required = evt.optJSONArray("rules")?.let { arr ->
                    (0 until arr.length()).map { arr.getJSONObject(it) }
                        .filter { it.optString("constraint") == "required" }.map { it.getString("field") }
                } ?: emptyList()
                _state.update { s ->
                    // show an input for every required field in the current rules
                    val f = s.fields.toMutableMap().apply { required.forEach { putIfAbsent(it, "") } }
                    s.copy(ui = ui, fields = f)
                }
            }
            "HEAL_RESOLVED" -> {
                _state.update { it.copy(missing = emptyList()) }
                notifier.clear()
            }
        }
    }

    fun onFieldChange(key: String, value: String) =
        _state.update { it.copy(fields = it.fields + (key to value)) }

    fun save() = viewModelScope.launch {
        // Normalise the phone number for the support dialler before sending
        val phone = _state.value.fields["phone_number"]!!.trim()  // MH-DEMO-BUG
        onFieldChange("phone_number", phone)
        _state.update { it.copy(saving = true, message = null) }
        try {
            val r = client.saveProfile(_state.value.profileId, _state.value.fields)
            val missing = r.optJSONArray("missing")?.toStringList() ?: emptyList()
            applyProfile(r)
            _state.update { it.copy(missing = missing, message = if (missing.isEmpty()) "Profile saved" else "Still missing: ${missing.joinToString()}") }
            if (missing.isEmpty()) notifier.clear()   // FR-5.4 clear alert
        } catch (e: Exception) {
            _state.update { it.copy(message = "Save failed: ${e.message}") }
        } finally {
            _state.update { it.copy(saving = false) }
        }
    }

    override fun onCleared() {
        socket?.close(1000, null)
    }
}
