package com.mobileheal.app.ui.order

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobileheal.app.di.ProfileId
import com.mobileheal.domain.model.AfterSave
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.usecase.ResolveNavigationUseCase
import com.mobileheal.domain.usecase.SaveProfileUseCase
import com.mobileheal.domain.usecase.ValidateProfileUseCase
import com.mobileheal.domain.repository.RulesRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class OrderViewModel @Inject constructor(
    private val rulesRepository: RulesRepository,
    private val validate: ValidateProfileUseCase,
    private val saveUseCase: SaveProfileUseCase,
    private val resolveNav: ResolveNavigationUseCase,
    @ProfileId private val profileId: Int,
) : ViewModel() {

    private val _state = MutableStateFlow<OrderUiState>(OrderUiState.Loading)
    val state: StateFlow<OrderUiState> = _state.asStateFlow()

    private val _effects = Channel<OrderEffect>(Channel.BUFFERED)
    val effects = _effects.receiveAsFlow()

    init {
        val rules = rulesRepository.rules.value
        _state.value = OrderUiState.Content(
            fields = rules.displayFields.associateWith { "" },
            missing = rules.requiredFields,
            rules = rules,
        )
    }

    fun onAction(action: OrderAction) {
        when (action) {
            is OrderAction.FieldChanged -> update { it.copy(fields = it.fields + (action.field to action.value)) }
            OrderAction.Submit -> submit()
        }
    }

    private fun submit() {
        val content = _state.value as? OrderUiState.Content ?: return
        if (content.saving) return
        val trimmed = content.fields.mapValues { it.value.trim() }
        val rules = content.rules
        val missing = validate(Profile(profileId, trimmed), rules)
        if (missing.isNotEmpty()) {
            update { it.copy(missing = missing, message = "Please fill required fields") }
            return
        }
        update { it.copy(saving = true, message = null) }
        viewModelScope.launch {
            saveUseCase(Profile(profileId, trimmed)).fold(
                onSuccess = { outcome ->
                    update { it.copy(saving = false, missing = outcome.missing, message = if (outcome.missing.isEmpty()) "Order submitted" else "Missing: ${outcome.missing.joinToString()}") }
                    when (val next = resolveNav(outcome.missing, rules)) {
                        AfterSave.Stay -> Unit
                        is AfterSave.Navigate -> _effects.send(OrderEffect.NavigateTo(next.screenId))
                    }
                },
                onFailure = { e -> update { it.copy(saving = false, message = e.message ?: "Submission failed") } }
            )
        }
    }

    private inline fun update(transform: (OrderUiState.Content) -> OrderUiState.Content) {
        val s = _state.value
        if (s is OrderUiState.Content) _state.value = transform(s)
    }
}
