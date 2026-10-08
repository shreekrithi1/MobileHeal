package com.mobileheal.app.ui

import androidx.compose.ui.graphics.Color

private val SMALL_WORDS = setOf("of", "and", "the", "or", "to", "in", "on", "for", "a", "an")

/** "date_of_birth" → "Date of Birth". */
fun String.toLabel(): String = split('_').filter { it.isNotBlank() }.mapIndexed { i, w ->
    if (i > 0 && w in SMALL_WORDS) w else w.replaceFirstChar { it.uppercaseChar() }
}.joinToString(" ")

/** Parses "#RRGGBB" / "#AARRGGBB" from the rules; falls back to [default] if absent or invalid. */
fun Map<String, String>.color(key: String, default: Color): Color {
    val hex = this[key]?.removePrefix("#") ?: return default
    val argb = when (hex.length) {
        6 -> hex.toLongOrNull(16)?.let { it or 0xFF000000 }
        8 -> hex.toLongOrNull(16)
        else -> null
    } ?: return default
    return Color(argb.toInt())
}
