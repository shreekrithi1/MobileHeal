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

    /** Fields to show: name and email always, then every field that has a rule. */
    val displayFields: List<String> get() = (listOf("name", "email") + fields.map { it.field }).distinct()

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
