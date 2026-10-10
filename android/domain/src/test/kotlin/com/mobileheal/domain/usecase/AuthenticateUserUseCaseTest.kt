package com.mobileheal.domain.usecase

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AuthenticateUserUseCaseTest {
    @Test fun `accepts demo credentials`() {
        val useCase = AuthenticateUserUseCase()
        assertTrue(useCase("test", "test"))
    }
    @Test fun `rejects wrong credentials`() {
        val useCase = AuthenticateUserUseCase()
        assertFalse(useCase("user", "bad"))
    }
}
