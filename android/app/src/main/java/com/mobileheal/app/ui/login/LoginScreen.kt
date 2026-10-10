package com.mobileheal.app.ui.login

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobileheal.app.BuildConfig

@Composable
fun LoginRoute(
    onLoggedIn: () -> Unit,
    viewModel: LoginViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) {
        viewModel.effects.collect { eff ->
            when (eff) {
                LoginEffect.LoggedIn -> onLoggedIn()
            }
        }
    }
    LoginScreen(state = state, onAction = viewModel::onAction)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LoginScreen(state: LoginUiState, onAction: (LoginAction) -> Unit) {
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }

    val submitting = state is LoginUiState.Submitting
    val error = (state as? LoginUiState.Error)?.message

    Scaffold(topBar = { TopAppBar(title = { Text("Restaurant Ordering (Demo)") }) }) { padding ->
        Column(
            Modifier.padding(padding).padding(24.dp).fillMaxSize(),
            verticalArrangement = Arrangement.Center
        ) {
            OutlinedTextField(
                value = username,
                onValueChange = { username = it; onAction(LoginAction.UsernameChanged(it)) },
                label = { Text("Username") },
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = password,
                onValueChange = { password = it; onAction(LoginAction.PasswordChanged(it)) },
                label = { Text("Password") },
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(16.dp))
            Button(
                onClick = { onAction(LoginAction.Submit) },
                enabled = !submitting,
                modifier = Modifier.fillMaxWidth()
            ) { Text(if (submitting) "Signing in…" else "Sign in") }
            if (!error.isNullOrEmpty()) {
                Spacer(Modifier.height(12.dp))
                Snackbar(containerColor = MaterialTheme.colorScheme.errorContainer) { Text(error, color = MaterialTheme.colorScheme.onErrorContainer) }
            }
            if (BuildConfig.DEBUG) {
                Spacer(Modifier.height(16.dp))
                Text("Demo login: test / test", color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            }
        }
    }
}

@Preview
@Composable
private fun LoginPreview() {
    MaterialTheme { LoginScreen(LoginUiState.Idle, onAction = {}) }
}
