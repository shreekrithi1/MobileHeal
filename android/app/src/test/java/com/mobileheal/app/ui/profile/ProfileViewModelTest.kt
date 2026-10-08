package com.mobileheal.app.ui.profile

import com.mobileheal.app.platform.HealAlerts
import com.mobileheal.domain.error.DomainError
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.FieldRule
import com.mobileheal.domain.model.LiveEvent
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.model.SaveOutcome
import com.mobileheal.domain.repository.LiveUpdatesRepository
import com.mobileheal.domain.repository.ProfileRepository
import com.mobileheal.domain.repository.RulesRepository
import com.mobileheal.domain.usecase.LoadProfileUseCase
import com.mobileheal.domain.usecase.ObserveLiveUpdatesUseCase
import com.mobileheal.domain.usecase.ResolveNavigationUseCase
import com.mobileheal.domain.usecase.SaveProfileUseCase
import com.mobileheal.domain.usecase.ValidateProfileUseCase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private class FakeProfiles(var stored: Profile, var missingOnSave: List<String> = emptyList(), var fail: DomainError? = null) : ProfileRepository {
    override suspend fun load(id: Int): Profile = fail?.let { throw it } ?: stored
    override suspend fun save(profile: Profile): SaveOutcome {
        fail?.let { throw it }
        stored = profile
        return SaveOutcome(profile, missingOnSave)
    }
}

private class FakeLive : LiveUpdatesRepository {
    val events = MutableSharedFlow<LiveEvent>(extraBufferCapacity = 8)
    override fun events(profileId: Int): Flow<LiveEvent> = events
}

private class FakeRules(initial: AppRules) : RulesRepository {
    private val flow = MutableStateFlow(initial)
    override val rules: StateFlow<AppRules> = flow
    override fun update(rules: AppRules) { flow.value = rules }
}

private class FakeAlerts : HealAlerts {
    var shown: List<String>? = null
    var cleared = 0
    override fun show(missing: List<String>) { shown = missing }
    override fun clear() { cleared++ }
}

@OptIn(ExperimentalCoroutinesApi::class)
class ProfileViewModelTest {
    private val dispatcher: TestDispatcher = StandardTestDispatcher()
    private val rules = AppRules(
        listOf(FieldRule("name", true), FieldRule("email", true), FieldRule("phone_number", true)),
        mapOf("after_save" to "success_screen"),
    )

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun vm(profiles: FakeProfiles, live: FakeLive = FakeLive(), alerts: FakeAlerts = FakeAlerts(), r: AppRules = rules) =
        ProfileViewModel(
            LoadProfileUseCase(profiles), SaveProfileUseCase(profiles), ObserveLiveUpdatesUseCase(live),
            ValidateProfileUseCase(), ResolveNavigationUseCase(), FakeRules(r), alerts, profileId = 1,
        )

    @Test fun `loads content and flags missing required fields`() = runTest(dispatcher) {
        val vm = vm(FakeProfiles(Profile(1, mapOf("name" to "Jane", "email" to "j@x.io"))))
        advanceUntilIdle()
        val s = vm.state.value as ProfileUiState.Content
        assertEquals(listOf("name", "email", "phone_number"), s.fields.keys.toList())
        assertEquals(listOf("phone_number"), s.missing)
    }

    @Test fun `load failure shows error and retry recovers`() = runTest(dispatcher) {
        val profiles = FakeProfiles(Profile(1, emptyMap()), fail = DomainError.Network())
        val vm = vm(profiles)
        advanceUntilIdle()
        assertTrue(vm.state.value is ProfileUiState.Error)
        profiles.fail = null
        vm.onAction(ProfileAction.Retry)
        advanceUntilIdle()
        assertTrue(vm.state.value is ProfileUiState.Content)
    }

    @Test fun `complete save navigates to the success screen and clears alerts`() = runTest(dispatcher) {
        val alerts = FakeAlerts()
        val vm = vm(FakeProfiles(Profile(1, mapOf("name" to "Jane", "email" to "j@x.io", "phone_number" to " 555 "))), alerts = alerts)
        advanceUntilIdle()
        vm.onAction(ProfileAction.Save)
        advanceUntilIdle()
        assertEquals(ProfileEffect.NavigateTo("success"), vm.effects.first())
        assertEquals(1, alerts.cleared)
        assertEquals("Profile saved", (vm.state.value as ProfileUiState.Content).message)
    }

    @Test fun `incomplete save stays on the profile`() = runTest(dispatcher) {
        val profiles = FakeProfiles(Profile(1, mapOf("name" to "Jane", "email" to "", "phone_number" to "555")), missingOnSave = listOf("email"))
        val vm = vm(profiles)
        advanceUntilIdle()
        vm.onAction(ProfileAction.Save)
        advanceUntilIdle()
        val s = vm.state.value as ProfileUiState.Content
        assertEquals(listOf("email"), s.missing)
        assertTrue(s.message!!.startsWith("Still missing"))
    }

    @Test fun `heal event adds the field and shows a notification`() = runTest(dispatcher) {
        val live = FakeLive()
        val alerts = FakeAlerts()
        val vm = vm(FakeProfiles(Profile(1, mapOf("name" to "Jane", "email" to "j@x.io", "phone_number" to "1"))), live, alerts)
        advanceUntilIdle()
        live.events.emit(LiveEvent.HealRequired(listOf("date_of_birth")))
        advanceUntilIdle()
        val s = vm.state.value as ProfileUiState.Content
        assertTrue("date_of_birth" in s.fields)
        assertEquals(listOf("date_of_birth"), alerts.shown)
    }

    @Test fun `rules update re-renders fields live`() = runTest(dispatcher) {
        val live = FakeLive()
        val vm = vm(FakeProfiles(Profile(1, mapOf("name" to "Jane", "email" to "j@x.io", "phone_number" to "1"))), live)
        advanceUntilIdle()
        live.events.emit(LiveEvent.RulesUpdated(rules.copy(fields = rules.fields + FieldRule("nickname", false))))
        advanceUntilIdle()
        assertTrue("nickname" in (vm.state.value as ProfileUiState.Content).fields)
    }
}
