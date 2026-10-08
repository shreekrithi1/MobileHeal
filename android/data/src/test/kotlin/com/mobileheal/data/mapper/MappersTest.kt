package com.mobileheal.data.mapper

import com.mobileheal.domain.model.AfterSave
import com.mobileheal.domain.model.LiveEvent
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MappersTest {
    @Test fun `profile json keeps dynamic attributes and drops metadata and nulls`() {
        val dto = JSONObject("""{"id":3,"name":"Jane","email":null,"date_of_birth":"1990-01-31","updated_at":"x","missing":["email"]}""").toProfileDto()
        assertEquals(mapOf("name" to "Jane", "date_of_birth" to "1990-01-31"), dto.fields)
        assertEquals(listOf("email"), dto.missing)
        assertEquals(3, dto.toDomain().id)
    }

    @Test fun `config event maps to rules with navigation`() {
        val e = JSONObject("""{"type":"CONFIG_UPDATED","rules":[{"field":"phone_number","constraint":"required"}],
            "ui":{"after_save":"success_screen","button_label":"Save changes"}}""").toLiveEvent()
        assertTrue(e is LiveEvent.RulesUpdated)
        val rules = (e as LiveEvent.RulesUpdated).rules
        assertEquals(listOf("phone_number"), rules.requiredFields)
        assertEquals(AfterSave.Navigate("success"), rules.afterSave)
    }

    @Test fun `heal events and unknown types`() {
        assertEquals(LiveEvent.HealRequired(listOf("phone_number")), JSONObject("""{"type":"HEAL_REQUIRED","missing":["phone_number"]}""").toLiveEvent())
        assertEquals(
            LiveEvent.HealRequired(listOf("email"), mapOf("email" to "not valid")),
            JSONObject("""{"type":"HEAL_REQUIRED","missing":["email"],"issues":{"email":"not valid"}}""").toLiveEvent(),
        )
        assertEquals(LiveEvent.HealResolved, JSONObject("""{"type":"HEAL_RESOLVED"}""").toLiveEvent())
        assertNull(JSONObject("""{"type":"PING"}""").toLiveEvent())
    }
}
