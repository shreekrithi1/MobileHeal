package com.mobileheal.app.ui.profile

import com.mobileheal.domain.model.AppRules
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
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeProfileRepository(initial: Profile) : ProfileRepository {
    var loadedId: Int? = null
    var lastSaved: Profile? = null
    private val initialProfile = initial
    override suspend fun load(id: Int): Profile {
        loadedId = id
        return initialProfile
    }
    override suspend fun save(profile: Profile): SaveOutcome {
        lastSaved = profile
        return SaveOutcome(profile = profile, missing = emptyList())
    }
}

private class FakeLiveUpdatesRepository : LiveUpdatesRepository {
    override fun events(profileId: Int): Flow<com.mobileheal.domain.model.LiveEvent> = emptyFlow()
}

private class FakeRulesRepository(initial: AppRules) : RulesRepository {
    private val _rules = MutableStateFlow(initial)
    override val rules: StateFlow<AppRules> get() = _rules
    override fun update(rules: AppRules) { _rules.value = rules }
}

private class NoopAlerts : com.mobileheal.app.platform.HealAlerts {
    override fun show(missing: List<String>, reasons: Map<String, String>) {}
    override fun clear() {}
}

@OptIn(ExperimentalCoroutinesApi::class)
class ProfileViewModelPhoneRemovalTest {

    @Test
    fun `save does not include phone_number when rule is removed`() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(dispatcher)
        try {
            val rules = AppRules(fields = emptyList(), ui = emptyMap())
            val rulesRepo = FakeRulesRepository(rules)
            val initial = Profile(
                id = 1,
                fields = mapOf(
                    "name" to "Alice",
                    "email" to "a@example.com",
                    "phone_number" to "+1 555 1000"
                )
            )
            val profileRepo = FakeProfileRepository(initial)
            val vm = ProfileViewModel(
                loadProfile = LoadProfileUseCase(profileRepo),
                saveProfile = SaveProfileUseCase(profileRepo),
                observeLiveUpdates = ObserveLiveUpdatesUseCase(FakeLiveUpdatesRepository()),
                validate = ValidateProfileUseCase(),
                resolveNavigation = ResolveNavigationUseCase(),
                rulesRepository = rulesRepo,
                alerts = NoopAlerts(),
                profileId = 1,
            )

            advanceUntilIdle()

            vm.onAction(ProfileAction.Save)
            advanceUntilIdle()

            val saved = profileRepo.lastSaved
            assertNotNull(saved)
            assertFalse(saved!!.fields.containsKey("phone_number"))
            assertTrue(saved.fields["name"] == "Alice")
            assertTrue(saved.fields["email"] == "a@example.com")
        } finally {
            Dispatchers.resetMain()
        }
    }
}
