package com.mobileheal.domain.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AppRulesTest {

    @Test
    fun displayFields_includesNameEvenWhenNoRules() {
        val rules = AppRules(emptyList(), emptyMap())
        assertEquals(listOf("name"), rules.displayFields)
    }

    @Test
    fun displayFields_omitsEmailWhenNotInRules() {
        val rules = AppRules(
            fields = listOf(
                FieldRule("name", true),
                FieldRule("date_of_birth", false),
                FieldRule("town", false),
            ),
            ui = emptyMap(),
        )
        val display = rules.displayFields
        assertTrue(display.first() == "name")
        assertFalse(display.contains("email"))
        assertEquals(listOf("name", "date_of_birth", "town"), display)
    }

    @Test
    fun displayFields_includesCityOnlyIfPresent() {
        val rules = AppRules(
            fields = listOf(FieldRule("city", false)),
            ui = emptyMap(),
        )
        val display = rules.displayFields
        assertTrue(display.contains("city"))
        assertEquals(listOf("name", "city"), display)
    }

    @Test
    fun afterSave_stayIsDefault() {
        val rules = AppRules(emptyList(), emptyMap())
        assertEquals(AfterSave.Stay, rules.afterSave)
    }

    @Test
    fun afterSave_mapsToSuccessScreen() {
        val rules = AppRules(emptyList(), mapOf("after_save" to "success_screen"))
        assertEquals(AfterSave.Navigate(AppRules.SUCCESS), rules.afterSave)
    }
}
