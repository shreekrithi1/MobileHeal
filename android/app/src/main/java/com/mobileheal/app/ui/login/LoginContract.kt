package com.mobileheal.app.ui.login

import androidx.compose.runtime.Immutable

@Immutable
sealed interface LoginUiState {
    data object Idle : LoginUiState
    data class Submitting(val username: String, val password: String) : LoginUiState
    data class Error(val username: String, val password: String, val message: String) : LoginUiState
}

sealed interface LoginAction {
    data class UsernameChanged(val value: String) : LoginAction
    data class PasswordChanged(val value: String) : LoginAction
    data object Submit : LoginAction
}

sealed interface LoginEffect {
    data object LoggedIn : LoginEffect
}
