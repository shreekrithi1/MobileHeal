package com.mobileheal.domain.model

/** One field rule from requirements.txt (`field: required|optional`). */
data class FieldRule(val field: String, val required: Boolean)

/** What the app does after a successful save (`ui.after_save`). */
sealed interface AfterSave {
    data object Stay : AfterSave
    data class Navigate(val screenId: String) : AfterSave
}

/** Content of a destination screen (`screen.<id>.title`, `screen.<id>.message`). */
data class ScreenSpec(val id: String, val title: String, val message: String)

/**
 * Business rules pushed live from the MobileHeal backend.
 * [ui] holds look-and-feel and navigation keys exactly as written in requirements.txt.
 */
data class AppRules(
    val fields: List<FieldRule>,
    val ui: Map<String, String>,
) {
    val requiredFields: List<String> get() = fields.filter { it.required }.map { it.field }

    /**
     * Fields to show in UI with consistent ordering:
     *  - name always first (even if absent from rules)
     *  - date_of_birth (if present in rules)
     *  - town (if present in rules)
     *  - city (if present in rules; legacy support)
     *  - followed by remaining rule-defined fields in their declared order
     */
    val displayFields: List<String>
        get() {
            val ruleFields = fields.map { it.field }
            val ordered = linkedSetOf<String>()
            // Always include primary identifier first
            ordered += "name"
            // UX-specified ordering preferences
            if ("date_of_birth" in ruleFields) ordered += "date_of_birth"
            // New preferred locality field
            if ("town" in ruleFields) ordered += "town"
            // Legacy locality field (only if explicitly present in rules)
            if ("city" in ruleFields) ordered += "city"
            // Append the rest in rule order
            ruleFields.forEach { f -> ordered += f }
            return ordered.toList()
        }

    val afterSave: AfterSave
        get() = when (val target = ui["after_save"]?.trim().orEmpty()) {
            "", "stay" -> AfterSave.Stay
            "success_screen" -> AfterSave.Navigate(SUCCESS)
            else -> AfterSave.Navigate(target)
        }

    fun screen(id: String): ScreenSpec =
        if (id == SUCCESS) {
            ScreenSpec(SUCCESS, ui["success_title"] ?: "Profile saved", ui["success_message"] ?: "Thanks — your details are up to date.")
        } else {
            ScreenSpec(id, ui["screen.$id.title"] ?: id.toTitle(), ui["screen.$id.message"].orEmpty())
        }

    companion object {
        const val SUCCESS = "success"
        val EMPTY = AppRules(emptyList(), emptyMap())
    }
}

internal fun String.toTitle(): String =
    split('_').filter { it.isNotBlank() }.joinToString(" ") { w -> w.replaceFirstChar { it.uppercaseChar() } }
