package com.mobileheal.app

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33) {
            registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
                .launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent { MaterialTheme { ProfileScreen() } }
    }
}

/** Parses "#RRGGBB" / "#AARRGGBB" from the rules; falls back to [default] if absent/invalid. */
fun Map<String, String>.color(key: String, default: Color): Color =
    this[key]?.let { runCatching { Color(android.graphics.Color.parseColor(it)) }.getOrNull() } ?: default

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProfileScreen(vm: ProfileViewModel = viewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val ui = s.ui
    Scaffold(containerColor = ui.color("background_color", MaterialTheme.colorScheme.background), topBar = {
        TopAppBar(title = { Text(ui["app_title"] ?: "MobileHeal") },
            colors = TopAppBarDefaults.topAppBarColors(containerColor = ui.color("background_color", MaterialTheme.colorScheme.surface)),
            actions = {
            Text(if (s.connected) "● Live" else "○ Offline",
                color = if (s.connected) Color(0xFF12B76A) else Color.Gray,
                modifier = Modifier.padding(end = 16.dp))
        })
    }) { pad ->
        Column(
            Modifier.padding(pad).padding(16.dp).fillMaxSize().verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            // FR-5.3 visible alert banner
            if (s.missing.isNotEmpty()) {
                val bannerText = ui.color("banner_text_color", Color(0xFFB54708))
                Card(colors = CardDefaults.cardColors(containerColor = ui.color("banner_color", Color(0xFFFFF4E5)))) {
                    Column(Modifier.padding(16.dp)) {
                        Text("Action needed", fontWeight = FontWeight.Bold, color = bannerText)
                        Text(ui["banner_message"] ?: ("Your profile is missing: ${s.missing.joinToString { it.toLabel() }}. " +
                             "Please fill in the highlighted fields and tap Save."), color = bannerText)
                    }
                }
            }
            Text("Your profile", style = MaterialTheme.typography.titleLarge)
            // Name & Email always shown (FR-5.2); extra required fields appear dynamically
            s.fields.forEach { (key, value) ->
                OutlinedTextField(
                    value = value,
                    onValueChange = { vm.onFieldChange(key, it) },
                    label = { Text(key.toLabel()) },
                    isError = key in s.missing && value.isBlank(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = when {
                        key.contains("email") -> KeyboardType.Email
                        key.contains("phone") -> KeyboardType.Phone
                        else -> KeyboardType.Text
                    }),
                    modifier = Modifier.fillMaxWidth()
                )
            }
            Button(
                onClick = { vm.save() }, enabled = !s.saving, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(
                    containerColor = ui.color("button_color", MaterialTheme.colorScheme.primary),
                    contentColor = ui.color("button_text_color", MaterialTheme.colorScheme.onPrimary),
                )
            ) {
                Text(if (s.saving) "Saving…" else (ui["button_label"] ?: "Save"))
            }
            s.message?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        }
    }
}
