package com.mobileheal.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobileheal.domain.usecase.AuthenticateUserUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class LoginViewModel @Inject constructor(
    private val authenticate: AuthenticateUserUseCase,
) : ViewModel() {

    private val _state = MutableStateFlow<LoginUiState>(LoginUiState.Idle)
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    private val _effects = Channel<LoginEffect>(Channel.BUFFERED)
    val effects = _effects.receiveAsFlow()

    private var username: String = ""
    private var password: String = ""

    fun onAction(action: LoginAction) {
        when (action) {
            is LoginAction.UsernameChanged -> username = action.value
            is LoginAction.PasswordChanged -> password = action.value
            LoginAction.Submit -> submit()
        }
    }

    private fun submit() {
        val u = username
        val p = password
        _state.value = LoginUiState.Submitting(u, p)
        viewModelScope.launch {
            val ok = authenticate(u, p)
            if (ok) {
                _effects.send(LoginEffect.LoggedIn)
                _state.value = LoginUiState.Idle
            } else {
                _state.value = LoginUiState.Error(u, p, "Invalid credentials")
            }
        }
    }
}
