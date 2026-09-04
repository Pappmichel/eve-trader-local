package com.pappmichel.evetraderlocal.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Always dark - New Eden's own UI has no light variant, and this app
 * mirrors that rather than following the system's light/dark setting (same
 * choice the desktop build's gui/theme.py makes by applying one fixed
 * stylesheet, not a light/dark pair). */
private val EveDarkColors = darkColorScheme(
    background = BgWindow,
    surface = BgPanel,
    surfaceVariant = BgField,
    primary = AccentCyan,
    onPrimary = Color.Black,
    secondary = AccentAmber,
    onSecondary = Color.Black,
    onBackground = TextPrimary,
    onSurface = TextPrimary,
    onSurfaceVariant = TextDim,
    outline = Border,
    outlineVariant = BorderLight,
    error = ErrorRed,
)

@Composable
fun EveTraderTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = EveDarkColors, content = content)
}
