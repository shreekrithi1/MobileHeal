package com.mobileheal.app.ui.profile

import androidx.compose.runtime.Immutable
import com.mobileheal.domain.model.AppRules

/** Single source of truth for the Profile screen (UDF). */
@Immutable
sealed interface ProfileUiState {
    data object Loading : ProfileUiState

    /** Rules define no fields to collect. */
    data class Empty(val rules: AppRules, val connected: Boolean) : ProfileUiState

    data class Content(
        val fields: Map<String, String>,   // display order: name, email, then rule fields
        val missing: List<String>,
        val rules: AppRules,
        val saving: Boolean = false,
        val message: String? = null,
        val connected: Boolean = false,
    ) : ProfileUiState

    data class Error(val message: String) : ProfileUiState
}

sealed interface ProfileAction {
    data class FieldChanged(val field: String, val value: String) : ProfileAction
    data object Save : ProfileAction
    data object Retry : ProfileAction
    data object MessageShown : ProfileAction
}

/** One-off events the UI must handle exactly once. */
sealed interface ProfileEffect {
    data class NavigateTo(val screenId: String) : ProfileEffect
}
