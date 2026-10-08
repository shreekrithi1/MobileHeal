package com.mobileheal.data.repository

import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.repository.RulesRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/** Latest rules: starts from the build-time defaults, then follows CONFIG_UPDATED events. */
@Singleton
class InMemoryRulesRepository @Inject constructor(initial: AppRules) : RulesRepository {
    private val state = MutableStateFlow(initial)
    override val rules: StateFlow<AppRules> = state.asStateFlow()
    override fun update(rules: AppRules) {
        state.value = rules
    }
}
