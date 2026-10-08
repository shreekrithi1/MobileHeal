package com.mobileheal.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.mobileheal.app.ui.color
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.ScreenSpec
import com.mobileheal.domain.repository.RulesRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import javax.inject.Inject

@Immutable
data class InfoUiState(val spec: ScreenSpec, val ui: Map<String, String>)

/** Backs any rule-defined destination (`screen.<id>.*`, or the built-in success screen). */
@HiltViewModel
class InfoViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    rulesRepository: RulesRepository,
) : ViewModel() {
    private val screenId: String = savedStateHandle.get<String>("id") ?: AppRules.SUCCESS
    val state: StateFlow<InfoUiState> = rulesRepository.rules
        .map { InfoUiState(it.screen(screenId), it.ui) }
        .stateIn(
            viewModelScope,
            SharingStarted.WhileSubscribed(5_000),
            rulesRepository.rules.value.let { InfoUiState(it.screen(screenId), it.ui) },
        )
}

@Composable
fun InfoRoute(onBack: () -> Unit, viewModel: InfoViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    InfoScreen(state, onBack)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InfoScreen(state: InfoUiState, onBack: () -> Unit) {
    val ui = state.ui
    val accent = ui.color("button_color", MaterialTheme.colorScheme.primary)
    Scaffold(
        containerColor = ui.color("background_color", MaterialTheme.colorScheme.background),
        topBar = { TopAppBar(title = { Text(ui["app_title"] ?: "MobileHeal") }) },
    ) { padding ->
        Column(
            Modifier.padding(padding).padding(24.dp).fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Surface(shape = CircleShape, color = accent, modifier = Modifier.size(72.dp)) {
                Box(contentAlignment = Alignment.Center) {
                    Text("✓", color = ui.color("button_text_color", Color.White), style = MaterialTheme.typography.headlineMedium)
                }
            }
            Spacer(Modifier.height(16.dp))
            Text(state.spec.title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
            if (state.spec.message.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(state.spec.message, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
            }
            Spacer(Modifier.height(24.dp))
            OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) { Text("Back to profile", color = accent) }
        }
    }
}

@Preview(name = "Success screen", showBackground = true)
@Composable
private fun InfoSuccessPreview() = MaterialTheme {
    InfoScreen(InfoUiState(AppRules.EMPTY.screen(AppRules.SUCCESS), mapOf("button_color" to "#079455")), onBack = {})
}

@Preview(name = "Custom screen, no message", showBackground = true)
@Composable
private fun InfoCustomPreview() = MaterialTheme {
    InfoScreen(InfoUiState(ScreenSpec("order_summary", "Order Summary", ""), emptyMap()), onBack = {})
}
