package com.pappmichel.evetraderlocal

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.pappmichel.evetraderlocal.ui.nav.AppNavHost
import com.pappmichel.evetraderlocal.ui.theme.EveTraderTheme

/** Single-activity app (standard modern-Android/Compose shape) - the
 * workspace-of-tabs model the desktop build's MainWindow uses
 * (gui/main_window.py) doesn't translate directly to a phone screen, so
 * navigation here is one screen at a time via a nav-drawer instead (see
 * ui/nav/AppNavHost.kt). Also the target for the EVE SSO redirect
 * (AndroidManifest.xml's second intent-filter) - `onNewIntent` is what
 * fires when the Custom Tab hands control back after login. */
class MainActivity : ComponentActivity() {
    private val app get() = application as EveTraderApplication

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleIntent(intent)
        setContent {
            EveTraderTheme {
                AppNavHost(tokenManager = app.tokenManager, database = app.database)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        val uri: Uri = intent?.data ?: return
        if (uri.scheme == "eveauth-eve-trader-local") {
            app.tokenManager.onRedirect(uri)
        }
    }
}
