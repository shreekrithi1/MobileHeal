package com.mobileheal.domain.usecase

import com.mobileheal.domain.error.DomainError
import com.mobileheal.domain.model.AfterSave
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.LiveEvent
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.model.SaveOutcome
import com.mobileheal.domain.repository.LiveUpdatesRepository
import com.mobileheal.domain.repository.ProfileRepository
import kotlinx.coroutines.flow.Flow
import kotlin.coroutines.cancellation.CancellationException

/** Required fields that are blank in [profile] under [rules]. */
class ValidateProfileUseCase {
    operator fun invoke(profile: Profile, rules: AppRules): List<String> =
        rules.requiredFields.filter { profile.value(it).isBlank() }
}

class LoadProfileUseCase(private val repository: ProfileRepository) {
    suspend operator fun invoke(id: Int): Result<Profile> = domainResult { repository.load(id) }
}

/** Trims values, then saves. The server re-validates and returns what is still missing. */
class SaveProfileUseCase(private val repository: ProfileRepository) {
    suspend operator fun invoke(profile: Profile): Result<SaveOutcome> = domainResult {
        repository.save(profile.copy(fields = profile.fields.mapValues { (_, v) -> v.trim() }))
    }
}

/** Where to go after a save: only navigate when nothing is missing. */
class ResolveNavigationUseCase {
    operator fun invoke(missing: List<String>, rules: AppRules): AfterSave =
        if (missing.isNotEmpty()) AfterSave.Stay else rules.afterSave
}

class ObserveLiveUpdatesUseCase(private val repository: LiveUpdatesRepository) {
    operator fun invoke(profileId: Int): Flow<LiveEvent> = repository.events(profileId)
}

/** Simple demo authentication use case. Replace with real backend auth when available. */
class AuthenticateUserUseCase {
    operator fun invoke(username: String, password: String): Boolean =
        username.trim() == "test" && password == "test"
}

/** Runs [block], keeping coroutine cancellation intact and wrapping unknown failures as [DomainError.Unexpected]. */
suspend inline fun <T> domainResult(crossinline block: suspend () -> T): Result<T> =
    try {
        Result.success(block())
    } catch (e: CancellationException) {
        throw e
    } catch (e: DomainError) {
        Result.failure(e)
    } catch (e: Exception) {
        Result.failure(DomainError.Unexpected(e))
    }
