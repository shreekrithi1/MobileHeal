package com.mobileheal.app.ui.login

import com.mobileheal.domain.usecase.AuthenticateUserUseCase
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelTest {
    @Test
    fun `login succeeds with demo credentials`() = runTest {
        val vm = LoginViewModel(AuthenticateUserUseCase())
        vm.onAction(LoginAction.UsernameChanged("test"))
        vm.onAction(LoginAction.PasswordChanged("test"))
        vm.onAction(LoginAction.Submit)
        val eff = vm.effects.first()
        assertEquals(LoginEffect.LoggedIn, eff)
    }

    @Test
    fun `login fails with wrong credentials`() = runTest {
        val vm = LoginViewModel(AuthenticateUserUseCase())
        vm.onAction(LoginAction.UsernameChanged("user"))
        vm.onAction(LoginAction.PasswordChanged("bad"))
        vm.onAction(LoginAction.Submit)
        // State should be Error
        val state = vm.state.value as LoginUiState.Error
        assertEquals("Invalid credentials", state.message)
    }
}
