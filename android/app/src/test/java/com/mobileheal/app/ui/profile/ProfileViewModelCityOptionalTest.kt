package com.mobileheal.app.ui.profile

import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.FieldRule
import com.mobileheal.domain.model.LiveEvent
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.repository.LiveUpdatesRepository
import com.mobileheal.domain.repository.ProfileRepository
import com.mobileheal.domain.repository.RulesRepository
import com.mobileheal.domain.usecase.LoadProfileUseCase
import com.mobileheal.domain.usecase.ObserveLiveUpdatesUseCase
import com.mobileheal.domain.usecase.ResolveNavigationUseCase
import com.mobileheal.domain.usecase.SaveProfileUseCase
import com.mobileheal.domain.usecase.ValidateProfileUseCase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain

class ProfileViewModelCityOptionalTest {

    @Test
    fun cityOptional_notFlaggedMissing() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(dispatcher)
        try {
            val rules = AppRules(
                fields = listOf(
                    FieldRule("name", true),
                    FieldRule("email", true),
                    FieldRule("date_of_birth", false),
                    FieldRule("city", false),
                ),
                ui = mapOf(
                    "button_color" to "#079455",
                    "button_label" to "Save changes",
                    "after_save" to "stay",
                    "banner_message" to "Changes saved successfully",
                    "banner_color" to "#FFF9C4",
                ),
            )

            val rulesRepository = FakeRulesRepository(rules)
            val profileRepository = FakeProfileRepository(
                profile = Profile(
                    id = 1,
                    fields = mapOf(
                        "name" to "Alice",
                        "email" to "alice@example.com",
                        // city is blank but optional per rules
                        "city" to "",
                    ),
                ),
            )
            val liveUpdatesRepository = FakeLiveUpdatesRepository()
            val alerts = FakeAlerts()

            val load = LoadProfileUseCase(profileRepository)
            val save = SaveProfileUseCase(profileRepository)
            val observe = ObserveLiveUpdatesUseCase(liveUpdatesRepository)
            val validate = ValidateProfileUseCase()
            val resolve = ResolveNavigationUseCase()

            val vm = ProfileViewModel(
                loadProfile = load,
                saveProfile = save,
                observeLiveUpdates = observe,
                validate = validate,
                resolveNavigation = resolve,
                rulesRepository = rulesRepository,
                alerts = alerts,
                profileId = 1,
            )

            advanceUntilIdle()

            val state = vm.state.value
            assertTrue(state is ProfileUiState.Content)
            state as ProfileUiState.Content
            // City is optional -> should not be reported missing even if blank
            assertTrue(state.missing.isEmpty())
            // Fields should include city as present in rules
            assertTrue("city" in state.fields)
            assertEquals("", state.fields["city"]) 
        } finally {
            Dispatchers.resetMain()
        }
    }

    // Fakes
    private class FakeProfileRepository(private val profile: Profile) : ProfileRepository {
        override suspend fun load(id: Int): Profile = profile
        override suspend fun save(profile: Profile) = throw AssertionError("save() not expected in this test")
    }

    private class FakeLiveUpdatesRepository : LiveUpdatesRepository {
        override fun events(profileId: Int): Flow<LiveEvent> = emptyFlow()
    }

    private class FakeRulesRepository(initial: AppRules) : RulesRepository {
        private val _rules = MutableStateFlow(initial)
        override val rules: StateFlow<AppRules> = _rules
        override fun update(rules: AppRules) { _rules.value = rules }
    }

    private class FakeAlerts : com.mobileheal.app.platform.HealAlerts {
        override fun show(missing: List<String>, reasons: Map<String, String>) {}
        override fun clear() {}
    }
}
