package com.mobileheal.app.ui.profile

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobileheal.app.di.ProfileId
import com.mobileheal.app.platform.HealAlerts
import com.mobileheal.domain.model.AfterSave
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.LiveEvent
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.repository.RulesRepository
import com.mobileheal.domain.usecase.LoadProfileUseCase
import com.mobileheal.domain.usecase.ObserveLiveUpdatesUseCase
import com.mobileheal.domain.usecase.ResolveNavigationUseCase
import com.mobileheal.domain.usecase.SaveProfileUseCase
import com.mobileheal.domain.usecase.ValidateProfileUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class ProfileViewModel @Inject constructor(
    private val loadProfile: LoadProfileUseCase,
    private val saveProfile: SaveProfileUseCase,
    private val observeLiveUpdates: ObserveLiveUpdatesUseCase,
    private val validate: ValidateProfileUseCase,
    private val resolveNavigation: ResolveNavigationUseCase,
    private val rulesRepository: RulesRepository,
    private val alerts: HealAlerts,
    @ProfileId private val profileId: Int,
) : ViewModel() {

    private val _state = MutableStateFlow<ProfileUiState>(ProfileUiState.Loading)
    val state: StateFlow<ProfileUiState> = _state.asStateFlow()

    private val _effects = Channel<ProfileEffect>(Channel.BUFFERED)
    val effects: Flow<ProfileEffect> = _effects.receiveAsFlow()

    private var connected = false

    init {
        load()
        observe()
    }

    fun onAction(action: ProfileAction) {
        when (action) {
            is ProfileAction.FieldChanged -> updateContent { it.copy(fields = it.fields + (action.field to action.value)) }
            ProfileAction.Save -> save()
            ProfileAction.Retry -> load()
            ProfileAction.MessageShown -> updateContent { it.copy(message = null) }
        }
    }

    private fun load() {
        _state.value = ProfileUiState.Loading
        viewModelScope.launch {
            loadProfile(profileId).fold(
                onSuccess = { profile -> _state.value = contentFor(profile.fields, rulesRepository.rules.value) },
                onFailure = { error -> _state.value = ProfileUiState.Error(error.message ?: "Couldn't load your profile") },
            )
        }
    }

    private fun observe() {
        viewModelScope.launch {
            observeLiveUpdates(profileId).collect { event ->
                when (event) {
                    is LiveEvent.HealRequired -> {
                        updateContent { c -> c.copy(fields = c.fields + event.missing.filterNot(c.fields::containsKey).associateWith { "" }, missing = event.missing) }
                        alerts.show(event.missing, event.reasons)
                    }
                    LiveEvent.HealResolved -> {
                        updateContent { it.copy(missing = emptyList()) }
                        alerts.clear()
                    }
                    is LiveEvent.RulesUpdated -> {
                        rulesRepository.update(event.rules)
                        val current = _state.value
                        val values = (current as? ProfileUiState.Content)?.fields.orEmpty()
                        if (current !is ProfileUiState.Loading && current !is ProfileUiState.Error) {
                            _state.value = contentFor(values, event.rules)
                        }
                    }
                    is LiveEvent.ConnectionChanged -> {
                        connected = event.connected
                        _state.update { s ->
                            when (s) {
                                is ProfileUiState.Content -> s.copy(connected = event.connected)
                                is ProfileUiState.Empty -> s.copy(connected = event.connected)
                                ProfileUiState.Loading -> s
                                is ProfileUiState.Error -> s
                            }
                        }
                    }
                }
            }
        }
    }

    private fun save() {
        val content = _state.value as? ProfileUiState.Content ?: return
        if (content.saving) return
        // Only normalize phone_number if it's part of the active fields; don't inject or overwrite when absent
        val fields = if ("phone_number" in content.fields) {
            val phone = content.fields.getValue("phone_number").trim()
            content.fields + ("phone_number" to phone)
        } else {
            content.fields
        }
        updateContent { it.copy(saving = true, message = null) }
        viewModelScope.launch {
            saveProfile(Profile(profileId, fields)).fold(
                onSuccess = { outcome ->
                    updateContent {
                        it.copy(
                            saving = false,
                            missing = outcome.missing,
                            message = if (outcome.missing.isEmpty()) "Profile saved" else "Still missing: ${outcome.missing.joinToString()}",
                        )
                    }
                    if (outcome.missing.isEmpty()) alerts.clear()
                    when (val next = resolveNavigation(outcome.missing, content.rules)) {
                        AfterSave.Stay -> Unit
                        is AfterSave.Navigate -> _effects.send(ProfileEffect.NavigateTo(next.screenId))
                    }
                },
                onFailure = { error -> updateContent { it.copy(saving = false, message = "Save failed: ${error.message}") } },
            )
        }
    }

    private fun contentFor(values: Map<String, String>, rules: AppRules): ProfileUiState {
        if (rules.fields.isEmpty() && values.isEmpty()) return ProfileUiState.Empty(rules, connected)
        val fields = linkedMapOf<String, String>()
        rules.displayFields.forEach { fields[it] = values[it].orEmpty() }
        val missing = validate(Profile(profileId, fields), rules)
        return ProfileUiState.Content(fields = fields, missing = missing, rules = rules, connected = connected)
    }

    private inline fun updateContent(transform: (ProfileUiState.Content) -> ProfileUiState.Content) {
        _state.update { s -> if (s is ProfileUiState.Content) transform(s) else s }
    }
}
