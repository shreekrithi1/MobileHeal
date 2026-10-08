package com.mobileheal.domain.usecase

import com.mobileheal.domain.error.DomainError
import com.mobileheal.domain.model.AfterSave
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.FieldRule
import com.mobileheal.domain.model.LiveEvent
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.model.SaveOutcome
import com.mobileheal.domain.repository.LiveUpdatesRepository
import com.mobileheal.domain.repository.ProfileRepository
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

private val rules = AppRules(
    fields = listOf(FieldRule("name", true), FieldRule("email", true), FieldRule("nickname", false)),
    ui = mapOf("after_save" to "success_screen"),
)

private class FakeProfileRepository(var failWith: Exception? = null) : ProfileRepository {
    var saved: Profile? = null
    override suspend fun load(id: Int): Profile = failWith?.let { throw it } ?: Profile(id, mapOf("name" to "Jane"))
    override suspend fun save(profile: Profile): SaveOutcome {
        failWith?.let { throw it }
        saved = profile
        return SaveOutcome(profile, emptyList())
    }
}

class ValidateProfileUseCaseTest {
    private val validate = ValidateProfileUseCase()

    @Test fun `blank and whitespace-only required fields are missing`() {
        assertEquals(listOf("email"), validate(Profile(1, mapOf("name" to "Jane", "email" to "  ")), rules))
    }

    @Test fun `optional fields are never missing`() {
        assertEquals(emptyList<String>(), validate(Profile(1, mapOf("name" to "Jane", "email" to "j@x.io")), rules))
    }

    @Test fun `no rules means nothing is missing`() {
        assertEquals(emptyList<String>(), validate(Profile(1, emptyMap()), AppRules.EMPTY))
    }
}

class SaveProfileUseCaseTest {
    @Test fun `values are trimmed before saving`() = runTest {
        val repo = FakeProfileRepository()
        SaveProfileUseCase(repo)(Profile(1, mapOf("name" to "  Jane  ")))
        assertEquals("Jane", repo.saved?.value("name"))
    }

    @Test fun `domain errors pass through unchanged`() = runTest {
        val result = SaveProfileUseCase(FakeProfileRepository(DomainError.Network()))(Profile(1, emptyMap()))
        assertTrue(result.exceptionOrNull() is DomainError.Network)
    }

    @Test fun `unknown failures become Unexpected`() = runTest {
        val result = LoadProfileUseCase(FakeProfileRepository(IllegalStateException("boom")))(1)
        assertTrue(result.exceptionOrNull() is DomainError.Unexpected)
    }
}

class ResolveNavigationUseCaseTest {
    private val resolve = ResolveNavigationUseCase()

    @Test fun `complete profile follows after_save`() {
        assertEquals(AfterSave.Navigate(AppRules.SUCCESS), resolve(emptyList(), rules))
    }

    @Test fun `missing fields keep the user on the profile`() {
        assertEquals(AfterSave.Stay, resolve(listOf("email"), rules))
    }

    @Test fun `custom screens and stay are supported`() {
        assertEquals(AfterSave.Navigate("order_summary"), resolve(emptyList(), rules.copy(ui = mapOf("after_save" to "order_summary"))))
        assertEquals(AfterSave.Stay, resolve(emptyList(), AppRules.EMPTY))
    }

    @Test fun `screen spec falls back to a readable title`() {
        assertEquals("Order Summary", AppRules.EMPTY.screen("order_summary").title)
    }
}

class LoadProfileUseCaseTest {
    @Test fun `returns the repository profile`() = runTest {
        assertEquals("Jane", LoadProfileUseCase(FakeProfileRepository())(7).getOrThrow().value("name"))
    }

    @Test fun `network failure is a typed error`() = runTest {
        assertTrue(LoadProfileUseCase(FakeProfileRepository(DomainError.Network()))(7).exceptionOrNull() is DomainError.Network)
    }
}

class ObserveLiveUpdatesUseCaseTest {
    private class RecordingLiveUpdates : LiveUpdatesRepository {
        var requested = -1
        override fun events(profileId: Int): Flow<LiveEvent> {
            requested = profileId
            return flowOf(LiveEvent.HealResolved)
        }
    }

    @Test fun `forwards repository events for the profile`() = runTest {
        val repo = RecordingLiveUpdates()
        val events = ObserveLiveUpdatesUseCase(repo)(42).toList()
        assertEquals(42, repo.requested)
        assertEquals(listOf<LiveEvent>(LiveEvent.HealResolved), events)
    }
}
