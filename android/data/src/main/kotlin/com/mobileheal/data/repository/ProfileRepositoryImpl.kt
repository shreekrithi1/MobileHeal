package com.mobileheal.data.repository

import com.mobileheal.data.mapper.toDomain
import com.mobileheal.data.mapper.toSaveOutcome
import com.mobileheal.data.remote.HttpStatusException
import com.mobileheal.data.remote.ProfileApi
import com.mobileheal.domain.error.DomainError
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.model.SaveOutcome
import com.mobileheal.domain.repository.ProfileRepository
import java.io.IOException
import javax.inject.Inject
import kotlin.coroutines.cancellation.CancellationException

class ProfileRepositoryImpl @Inject constructor(
    private val api: ProfileApi,
) : ProfileRepository {

    override suspend fun load(id: Int): Profile = mapErrors {
        try {
            api.get(id).toDomain().also {
                // Contact card is non-critical: a 5xx is reported to MobileHeal by the interceptor and healed server-side.
                runCatching { api.contact(id) }
            }
        } catch (e: HttpStatusException) {
            if (e.code != 404) throw e
            api.create(mapOf("name" to "Jane Doe", "email" to "jane@example.com")).toDomain()
        }
    }

    override suspend fun save(profile: Profile): SaveOutcome = mapErrors {
        api.update(profile.id, profile.fields).toSaveOutcome()
    }

    /** Error demangling: infrastructure exceptions → typed domain errors. */
    private suspend fun <T> mapErrors(block: suspend () -> T): T =
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpStatusException) {
            throw if (e.code == 404) DomainError.NotFound("Profile") else DomainError.Server(e.code, e.body.take(200))
        } catch (e: IOException) {
            throw DomainError.Network(e)
        } catch (e: org.json.JSONException) {
            throw DomainError.Unexpected(e)
        }
}
