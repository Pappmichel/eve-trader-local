package com.pappmichel.evetraderlocal.data.auth

import android.content.Context
import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent
import com.pappmichel.evetraderlocal.BuildConfig
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.TokenEntity
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request

/** Same redirect this app's AndroidManifest.xml registers an intent-filter
 * for - must exactly match what's registered for this client id at
 * https://developers.eveonline.com (the direct counterpart of the desktop
 * build's .env EVE_SSO_CALLBACK_* / http://localhost:8000/callback). */
const val REDIRECT_URI = "eveauth-eve-trader-local://callback"

private const val AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
private const val TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
private const val VERIFY_URL = "https://login.eveonline.com/oauth/verify"

@Serializable
data class TokenRecord(
    val role: String,               // e.g. "buyer:123456789" - matches auth.py's TokenRecord.role shape
    val characterId: Long,
    val characterName: String,
    val accessToken: String,
    val refreshToken: String,
    val expiresAt: Long,            // unix seconds
    val scopes: String,
) {
    fun isExpired(skewSeconds: Long = 60): Boolean =
        System.currentTimeMillis() / 1000 >= (expiresAt - skewSeconds)
}

/** Same authorization-code + PKCE flow as the desktop build's auth.py,
 * adapted for Android: a Chrome Custom Tab plus a custom URI scheme
 * redirect (see MainActivity.onNewIntent/AndroidManifest.xml) instead of a
 * loopback http.server. There is still no server anywhere in this flow -
 * that "no backend, ever" property is what actually matters for this
 * app's design, and it holds on both platforms even though the mechanics
 * of catching the redirect differ.
 *
 * One request in flight at a time, same as the desktop build - `login()`
 * suspends on `pendingRedirect` until `onRedirect` (called from
 * MainActivity) delivers the callback Uri or the caller cancels/times out. */
class TokenManager(private val context: Context, private val db: AppDatabase) {
    private val http = OkHttpClient()
    private val json = Json { ignoreUnknownKeys = true }
    private var pendingRedirect: CompletableDeferred<Uri>? = null

    fun onRedirect(uri: Uri) {
        pendingRedirect?.complete(uri)
    }

    suspend fun login(rolePrefix: String, scopes: List<String>): TokenRecord {
        val clientId = BuildConfig.EVE_SSO_CLIENT_ID
        require(clientId.isNotBlank()) {
            "EVE_SSO_CLIENT_ID is not set - put it in local.properties (see local.properties.example)."
        }
        val state = randomUrlSafe(16)
        val (verifier, challenge) = makePkcePair()

        val authorizeUri = Uri.parse(AUTHORIZE_URL).buildUpon()
            .appendQueryParameter("response_type", "code")
            .appendQueryParameter("redirect_uri", REDIRECT_URI)
            .appendQueryParameter("client_id", clientId)
            .appendQueryParameter("scope", scopes.joinToString(" "))
            .appendQueryParameter("state", state)
            .appendQueryParameter("code_challenge", challenge)
            .appendQueryParameter("code_challenge_method", "S256")
            .build()

        val deferred = CompletableDeferred<Uri>()
        pendingRedirect = deferred
        withContext(Dispatchers.Main) {
            CustomTabsIntent.Builder().build().launchUrl(context, authorizeUri)
        }
        val redirect = try {
            deferred.await()
        } finally {
            pendingRedirect = null
        }

        val code = redirect.getQueryParameter("code")
            ?: error("SSO login failed: ${redirect.getQueryParameter("error_description") ?: "no code returned"}")
        require(redirect.getQueryParameter("state") == state) {
            "SSO state mismatch - possible CSRF, aborting."
        }

        return withContext(Dispatchers.IO) {
            val tokenResponse = exchangeCode(code, verifier, clientId)
            val (characterId, characterName) = verifyAccessToken(tokenResponse.accessToken)
            val record = TokenRecord(
                role = "$rolePrefix:$characterId",
                characterId = characterId,
                characterName = characterName,
                accessToken = tokenResponse.accessToken,
                refreshToken = tokenResponse.refreshToken ?: "",
                expiresAt = System.currentTimeMillis() / 1000 + (tokenResponse.expiresIn ?: 1200),
                scopes = scopes.joinToString(" "),
            )
            saveRecord(record)
            record
        }
    }

    suspend fun getToken(role: String): TokenRecord = withContext(Dispatchers.IO) {
        val record = loadRecord(role) ?: error("No stored token for role '$role'. Log in first.")
        if (record.isExpired()) refresh(record) else record
    }

    suspend fun listRecords(prefix: String? = null): List<TokenRecord> = withContext(Dispatchers.IO) {
        db.tokenDao().getAll()
            .map { json.decodeFromString<TokenRecord>(it.recordJson) }
            .filter { prefix == null || it.role.startsWith("$prefix:") }
            .sortedBy { it.role }
    }

    suspend fun removeToken(role: String) = withContext(Dispatchers.IO) {
        db.tokenDao().delete(role)
    }

    private suspend fun refresh(record: TokenRecord): TokenRecord {
        val clientId = BuildConfig.EVE_SSO_CLIENT_ID
        val body = FormBody.Builder()
            .add("grant_type", "refresh_token")
            .add("refresh_token", record.refreshToken)
            .add("client_id", clientId)
            .build()
        http.newCall(Request.Builder().url(TOKEN_URL).post(body).build()).execute().use { response ->
            if (!response.isSuccessful) {
                error("Refreshing the token for '${record.role}' failed (${response.code}). Log in again.")
            }
            val tokenResponse = json.decodeFromString<TokenResponse>(response.body!!.string())
            val newRecord = record.copy(
                accessToken = tokenResponse.accessToken,
                refreshToken = tokenResponse.refreshToken ?: record.refreshToken,
                expiresAt = System.currentTimeMillis() / 1000 + (tokenResponse.expiresIn ?: 1200),
            )
            saveRecord(newRecord)
            return newRecord
        }
    }

    private fun exchangeCode(code: String, verifier: String, clientId: String): TokenResponse {
        val body = FormBody.Builder()
            .add("grant_type", "authorization_code")
            .add("code", code)
            .add("client_id", clientId)
            .add("code_verifier", verifier)
            .build()
        http.newCall(Request.Builder().url(TOKEN_URL).post(body).build()).execute().use { response ->
            require(response.isSuccessful) { "EVE SSO token exchange failed (${response.code})." }
            return json.decodeFromString(response.body!!.string())
        }
    }

    private fun verifyAccessToken(accessToken: String): Pair<Long, String> {
        val request = Request.Builder().url(VERIFY_URL).header("Authorization", "Bearer $accessToken").build()
        http.newCall(request).execute().use { response ->
            require(response.isSuccessful) { "Could not verify the EVE SSO access token (${response.code})." }
            val verify = json.decodeFromString<VerifyResponse>(response.body!!.string())
            return verify.characterId to verify.characterName
        }
    }

    private suspend fun saveRecord(record: TokenRecord) {
        db.tokenDao().upsert(TokenEntity(role = record.role, recordJson = json.encodeToString(record)))
    }

    private suspend fun loadRecord(role: String): TokenRecord? =
        db.tokenDao().get(role)?.let { json.decodeFromString(it.recordJson) }
}

@Serializable
private data class TokenResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String? = null,
    @SerialName("expires_in") val expiresIn: Long? = null,
)

@Serializable
private data class VerifyResponse(
    @SerialName("CharacterID") val characterId: Long,
    @SerialName("CharacterName") val characterName: String,
)

private fun randomUrlSafe(numBytes: Int): String {
    val bytes = ByteArray(numBytes)
    SecureRandom().nextBytes(bytes)
    return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
}

/** (verifier, challenge) - same RFC 7636 shape as auth.py's make_pkce_pair,
 * unpadded base64url on both halves (EVE SSO rejects the exchange
 * otherwise). */
private fun makePkcePair(): Pair<String, String> {
    val verifier = randomUrlSafe(64)
    val digest = MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII))
    val challenge = Base64.getUrlEncoder().withoutPadding().encodeToString(digest)
    return verifier to challenge
}
