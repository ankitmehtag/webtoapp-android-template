package com.example.webtoapp

import androidx.compose.ui.graphics.Color

/**
 * Single source of truth for every customisable value.
 * CI patches this file via sed — do NOT move the constants or change their format.
 */
object Config {
    // ── URL ──────────────────────────────────────────────────────────────
    const val URL = "https://www.google.com"

    // ── Branding ─────────────────────────────────────────────────────────
    const val APP_NAME      = "WebToApp"
    const val PRIMARY_COLOR = "#6366F1"

    // ── Splash screen ────────────────────────────────────────────────────
    // SPLASH_BG_COLOR = #6366F1
    const val SPLASH_BG_COLOR = "#6366F1"

    // ── Derived helpers ───────────────────────────────────────────────────
    val primaryColor: Color
        get() = try {
            val argb = android.graphics.Color.parseColor(PRIMARY_COLOR)
            Color(argb)
        } catch (_: Exception) {
            Color(0xFF6366F1.toInt())
        }
}
