package com.mobileheal.domain.repository

import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.LiveEvent
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.model.SaveOutcome
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

/** Implementations throw [com.mobileheal.domain.error.DomainError] subtypes only. */
interface ProfileRepository {
    suspend fun load(id: Int): Profile
    suspend fun save(profile: Profile): SaveOutcome
}

interface LiveUpdatesRepository {
    /** Hot stream of backend events; reconnects on its own. */
    fun events(profileId: Int): Flow<LiveEvent>
}

interface RulesRepository {
    val rules: StateFlow<AppRules>
    fun update(rules: AppRules)
}
