package com.mobileheal.app.ui.profile

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobileheal.app.ui.color
import com.mobileheal.app.ui.toLabel
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.FieldRule

/** Stateful entry point: wires the ViewModel to the stateless [ProfileScreen]. */
@Composable
fun ProfileRoute(onNavigate: (screenId: String) -> Unit, viewModel: ProfileViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(viewModel) {
        viewModel.effects.collect { effect ->
            when (effect) {
                is ProfileEffect.NavigateTo -> onNavigate(effect.screenId)
            }
        }
    }
    ProfileScreen(state = state, onAction = viewModel::onAction)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProfileScreen(state: ProfileUiState, onAction: (ProfileAction) -> Unit) {
    val ui = when (state) {
        is ProfileUiState.Content -> state.rules.ui
        is ProfileUiState.Empty -> state.rules.ui
        ProfileUiState.Loading -> emptyMap()
        is ProfileUiState.Error -> emptyMap()
    }
    val connected = when (state) {
        is ProfileUiState.Content -> state.connected
        is ProfileUiState.Empty -> state.connected
        ProfileUiState.Loading -> false
        is ProfileUiState.Error -> false
    }
    Scaffold(
        containerColor = ui.color("background_color", MaterialTheme.colorScheme.background),
        topBar = {
            TopAppBar(
                title = { Text(ui["app_title"] ?: "MobileHeal") },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = ui.color("background_color", MaterialTheme.colorScheme.surface)),
                actions = {
                    Text(
                        if (connected) "● Live" else "○ Offline",
                        color = if (connected) Color(0xFF12B76A) else Color.Gray,
                        modifier = Modifier.padding(end = 16.dp),
                    )
                },
            )
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when (state) {
                ProfileUiState.Loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                is ProfileUiState.Error -> ErrorBody(state.message, onRetry = { onAction(ProfileAction.Retry) })
                is ProfileUiState.Empty -> EmptyBody()
                is ProfileUiState.Content -> ContentBody(state, onAction)
            }
        }
    }
}

@Composable
private fun ContentBody(state: ProfileUiState.Content, onAction: (ProfileAction) -> Unit) {
    val ui = state.rules.ui
    Column(
        Modifier.padding(16.dp).fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (state.missing.isNotEmpty()) {
            val bannerText = ui.color("banner_text_color", Color(0xFFB54708))
            Card(colors = CardDefaults.cardColors(containerColor = ui.color("banner_color", Color(0xFFFFF4E5)))) {
                Column(Modifier.padding(16.dp)) {
                    Text("Action needed", fontWeight = FontWeight.Bold, color = bannerText)
                    Text(
                        // a custom message never hides *which* fields are missing
                        ui["banner_message"]?.let { "$it Missing: ${state.missing.joinToString { f -> f.toLabel() }}." }
                            ?: "Your profile is missing: ${state.missing.joinToString { it.toLabel() }}. " +
                            "Please fill in the highlighted fields and tap ${ui["button_label"] ?: "Save"}.",
                        color = bannerText,
                    )
                }
            }
        }
        Text("Your profile", style = MaterialTheme.typography.titleLarge)
        state.fields.forEach { (field, value) ->
            val required = state.rules.requiredFields.contains(field)
            OutlinedTextField(
                value = value,
                onValueChange = { onAction(ProfileAction.FieldChanged(field, it)) },
                label = { Text(field.toLabel() + if (required) " *" else "") },
                isError = field in state.missing && value.isBlank(),
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = when {
                        "email" in field -> KeyboardType.Email
                        "phone" in field -> KeyboardType.Phone
                        else -> KeyboardType.Text
                    },
                ),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        Button(
            onClick = { onAction(ProfileAction.Save) },
            enabled = !state.saving,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(
                containerColor = ui.color("button_color", MaterialTheme.colorScheme.primary),
                contentColor = ui.color("button_text_color", MaterialTheme.colorScheme.onPrimary),
            ),
        ) {
            Text(if (state.saving) "Saving…" else ui["button_label"] ?: "Save")
        }
        state.message?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
private fun ErrorBody(message: String, onRetry: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Couldn't load your profile", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(8.dp))
        Text(message, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
        Spacer(Modifier.height(16.dp))
        OutlinedButton(onClick = onRetry) { Text("Try again") }
    }
}

@Composable
private fun EmptyBody() {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Nothing to fill in", style = MaterialTheme.typography.titleMedium)
        Text("No profile fields are configured yet.", style = MaterialTheme.typography.bodyMedium)
    }
}

// ---------------------------------------------------------------- previews (every UI state)
private val previewRules = AppRules(
    fields = listOf(FieldRule("name", true), FieldRule("email", true), FieldRule("phone_number", true)),
    ui = mapOf("button_label" to "Save changes", "button_color" to "#079455"),
)

@Preview(name = "Loading", showBackground = true)
@Composable
private fun ProfileLoadingPreview() = MaterialTheme { ProfileScreen(ProfileUiState.Loading) {} }

@Preview(name = "Success · missing phone", showBackground = true)
@Composable
private fun ProfileContentPreview() = MaterialTheme {
    ProfileScreen(
        ProfileUiState.Content(
            fields = linkedMapOf("name" to "Jane Doe", "email" to "jane@example.com", "phone_number" to ""),
            missing = listOf("phone_number"),
            rules = previewRules,
            connected = true,
        ),
    ) {}
}

@Preview(name = "Empty", showBackground = true)
@Composable
private fun ProfileEmptyPreview() = MaterialTheme { ProfileScreen(ProfileUiState.Empty(AppRules.EMPTY, connected = true)) {} }

@Preview(name = "Error", showBackground = true)
@Composable
private fun ProfileErrorPreview() = MaterialTheme { ProfileScreen(ProfileUiState.Error("Can't reach the server. Check your connection.")) {} }
