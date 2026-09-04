package com.pappmichel.evetraderlocal

import android.app.Application
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.db.AppDatabase

/** No dependency-injection framework - one Application-scoped instance of
 * the database and the auth manager, handed to Activities/Composables
 * directly. Matches this project's own "dependency-light" discipline on
 * the desktop side (pyproject.toml's own comment on that); a single-user,
 * single-process local app has no real need for Hilt/Koin here. */
class EveTraderApplication : Application() {
    lateinit var database: AppDatabase
        private set
    lateinit var tokenManager: TokenManager
        private set

    override fun onCreate() {
        super.onCreate()
        database = AppDatabase.get(this)
        tokenManager = TokenManager(this, database)
    }
}
