package com.mobileheal.app.ui.order

import androidx.compose.runtime.Immutable
import com.mobileheal.domain.model.AppRules

@Immutable
sealed interface OrderUiState {
    data object Loading : OrderUiState
    data class Content(
        val fields: Map<String, String>,
        val missing: List<String>,
        val rules: AppRules,
        val saving: Boolean = false,
        val message: String? = null,
    ) : OrderUiState
    data class Error(val message: String) : OrderUiState
}

sealed interface OrderAction {
    data class FieldChanged(val field: String, val value: String) : OrderAction
    data object Submit : OrderAction
}

sealed interface OrderEffect {
    data class NavigateTo(val screenId: String) : OrderEffect
}
