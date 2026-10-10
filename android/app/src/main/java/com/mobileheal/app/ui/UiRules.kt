package com.mobileheal.app.ui

import androidx.compose.ui.graphics.Color
import kotlin.math.abs

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

/** Relative luminance based contrast ratio per WCAG. */
private fun contrastRatio(fg: Color, bg: Color): Double {
    fun channel(c: Float): Double {
        val v = c / 255f
        return if (v <= 0.03928f) (v / 12.92f).toDouble() else Math.pow(((v + 0.055f) / 1.055f).toDouble(), 2.4)
    }
    val r1 = channel((fg.red * 255).coerceIn(0f, 255f))
    val g1 = channel((fg.green * 255).coerceIn(0f, 255f))
    val b1 = channel((fg.blue * 255).coerceIn(0f, 255f))
    val l1 = 0.2126 * r1 + 0.7152 * g1 + 0.0722 * b1

    val r2 = channel((bg.red * 255).coerceIn(0f, 255f))
    val g2 = channel((bg.green * 255).coerceIn(0f, 255f))
    val b2 = channel((bg.blue * 255).coerceIn(0f, 255f))
    val l2 = 0.2126 * r2 + 0.7152 * g2 + 0.0722 * b2

    val (lMax, lMin) = if (l1 >= l2) l1 to l2 else l2 to l1
    return (lMax + 0.05) / (lMin + 0.05)
}

/** Ensures button text color meets AA 4.5:1 against the button color. Prefers provided value, else black/white fallback. */
fun Map<String, String>.buttonTextOn(buttonKey: String, textKey: String, default: Color): Color {
    val bg = color(buttonKey, Color.Unspecified).let { if (it == Color.Unspecified) default else it }
    val proposed = color(textKey, default)
    return if (contrastRatio(proposed, bg) >= 4.5) proposed else {
        val black = Color(0xFF000000.toInt())
        if (contrastRatio(black, bg) >= 4.5) black else Color(0xFFFFFFFF.toInt())
    }
}
