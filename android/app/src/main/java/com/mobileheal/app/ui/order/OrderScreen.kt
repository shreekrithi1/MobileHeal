package com.mobileheal.app.ui.order

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobileheal.app.ui.buttonTextOn
import com.mobileheal.app.ui.color
import com.mobileheal.app.ui.toLabel

@Composable
fun OrderRoute(
    onNavigate: (String) -> Unit,
    viewModel: OrderViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) {
        viewModel.effects.collect { eff ->
            when (eff) {
                is OrderEffect.NavigateTo -> onNavigate(eff.screenId)
            }
        }
    }
    when (val s = state) {
        is OrderUiState.Content -> OrderScreen(s, onAction = viewModel::onAction)
        is OrderUiState.Error -> ErrorScreen(s.message)
        OrderUiState.Loading -> LoadingScreen()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LoadingScreen() {
    Scaffold(topBar = { TopAppBar(title = { Text("Loading…") }) }) { padding ->
        Text("Loading…", modifier = Modifier.padding(padding).padding(24.dp))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ErrorScreen(message: String) {
    Scaffold(topBar = { TopAppBar(title = { Text("Error") }) }) { padding ->
        Text(message, modifier = Modifier.padding(padding).padding(24.dp), color = MaterialTheme.colorScheme.error)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrderScreen(state: OrderUiState.Content, onAction: (OrderAction) -> Unit) {
    val ui = state.rules.ui
    val buttonColor = ui.color("button_color", MaterialTheme.colorScheme.primary)
    val buttonText = ui.buttonTextOn("button_color", "button_text_color", Color.Black)

    Scaffold(topBar = { TopAppBar(title = { Text(ui["app_title"] ?: "Order") }) }) { padding ->
        Column(Modifier.padding(padding).padding(24.dp).fillMaxSize()) {
            state.fields.forEach { (k, v) ->
                val isMissing = k in state.missing
                OutlinedTextField(
                    value = v,
                    onValueChange = { onAction(OrderAction.FieldChanged(k, it)) },
                    label = { Text(k.toLabel()) },
                    isError = isMissing,
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(Modifier.height(12.dp))
            }
            if (!state.message.isNullOrEmpty()) {
                Snackbar { Text(state.message!!, color = MaterialTheme.colorScheme.onSecondaryContainer) }
                Spacer(Modifier.height(12.dp))
            }
            Button(
                onClick = { onAction(OrderAction.Submit) },
                enabled = !state.saving,
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(
                    containerColor = buttonColor,
                    contentColor = buttonText,
                )
            ) { Text(ui["button_label"] ?: "Place order") }
        }
    }
}

@Preview
@Composable
private fun OrderPreview() {
    val rules = com.mobileheal.domain.model.AppRules(
        fields = listOf(
            com.mobileheal.domain.model.FieldRule("name", true),
            com.mobileheal.domain.model.FieldRule("email", true),
            com.mobileheal.domain.model.FieldRule("restaurant_name", true),
            com.mobileheal.domain.model.FieldRule("menu_item", true),
            com.mobileheal.domain.model.FieldRule("quantity", true),
            com.mobileheal.domain.model.FieldRule("fulfillment_method", true),
        ),
        ui = mapOf("button_color" to "#079455", "app_title" to "Restaurant Ordering (Demo)")
    )
    MaterialTheme {
        OrderScreen(
            OrderUiState.Content(
                fields = rules.displayFields.associateWith { "" },
                missing = rules.requiredFields,
                rules = rules
            ),
            onAction = {}
        )
    }
}
