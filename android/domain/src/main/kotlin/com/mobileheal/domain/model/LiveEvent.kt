package com.mobileheal.domain.model

/** Real-time events from the MobileHeal backend (WebSocket). */
sealed interface LiveEvent {
    /** [reasons]: field → what's wrong, from the DataWatchdog (e.g. "“12” isn't a valid phone number"). */
    data class HealRequired(val missing: List<String>, val reasons: Map<String, String> = emptyMap()) : LiveEvent
    data object HealResolved : LiveEvent
    data class RulesUpdated(val rules: AppRules) : LiveEvent
    data class ConnectionChanged(val connected: Boolean) : LiveEvent
}
