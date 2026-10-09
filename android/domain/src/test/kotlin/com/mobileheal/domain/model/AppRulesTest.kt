package com.mobileheal.domain.model

import com.mobileheal.domain.usecase.ValidateProfileUseCase
import org.junit.Assert.assertEquals
import org.junit.Test

class AppRulesTest {
    @Test
    fun `displayFields orders city after date_of_birth`() {
        val rules = AppRules(
            fields = listOf(
                FieldRule("name", true),
                FieldRule("email", true),
                FieldRule("date_of_birth", false),
                FieldRule("city", true),
                FieldRule("newsletter_opt_in", false),
            ),
            ui = emptyMap(),
        )
        val order = rules.displayFields
        assertEquals(listOf("name", "email", "date_of_birth", "city", "newsletter_opt_in"), order)
    }

    @Test
    fun `validate marks city missing when required and blank`() {
        val rules = AppRules(
            fields = listOf(
                FieldRule("name", true),
                FieldRule("email", true),
                FieldRule("date_of_birth", false),
                FieldRule("city", true),
            ),
            ui = emptyMap(),
        )
        val profile = Profile(
            id = 1,
            fields = mapOf(
                "name" to "Alice",
                "email" to "alice@example.com",
                "date_of_birth" to "2000-01-01",
                "city" to "",
            ),
        )
        val missing = ValidateProfileUseCase().invoke(profile, rules)
        assertEquals(listOf("city"), missing)
    }
}
