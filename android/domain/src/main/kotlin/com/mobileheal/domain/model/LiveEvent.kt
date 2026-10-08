package com.mobileheal.domain.model

/** Real-time events from the MobileHeal backend (WebSocket). */
sealed interface LiveEvent {
    data class HealRequired(val missing: List<String>) : LiveEvent
    data object HealResolved : LiveEvent
    data class RulesUpdated(val rules: AppRules) : LiveEvent
    data class ConnectionChanged(val connected: Boolean) : LiveEvent
}
