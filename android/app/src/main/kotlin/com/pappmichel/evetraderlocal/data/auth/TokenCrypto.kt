package com.pappmichel.evetraderlocal.data.auth

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Encrypts `TokenRecord` JSON at rest (AES-256-GCM, key held in the
 * Android Keystore - non-exportable, per-device, never touches app code or
 * disk in plaintext) before `TokenManager` writes it into Room's
 * `tokens` table. Closes the gap `android/README.md` used to list under
 * "Honest limitations": Room otherwise writes a plain SQLite file, same as
 * the desktop build's own plain SQLite `tokens` table - fine for a
 * single-user local app in the same sense it already is on desktop, but
 * an EVE SSO refresh token is a real bearer credential (it's what lets an
 * attacker with file access mint fresh access tokens indefinitely), so
 * it's worth the small amount of code this takes.
 *
 * Stores ciphertext as `base64(iv):base64(ciphertext)` in the same
 * `recordJson` column TokenEntity already had - no Room schema/migration
 * change needed. */
object TokenCrypto {
    private const val ANDROID_KEYSTORE = "AndroidKeyStore"
    private const val KEY_ALIAS = "eve_trader_local_token_key"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val GCM_TAG_LENGTH_BITS = 128

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }

        val keyGenerator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        keyGenerator.init(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return keyGenerator.generateKey()
    }

    fun encrypt(plainText: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, getOrCreateKey()) }
        val cipherText = cipher.doFinal(plainText.toByteArray(Charsets.UTF_8))
        val iv = Base64.getEncoder().encodeToString(cipher.iv)
        val body = Base64.getEncoder().encodeToString(cipherText)
        return "$iv:$body"
    }

    fun decrypt(stored: String): String {
        val (ivPart, bodyPart) = stored.split(":", limit = 2)
        val iv = Base64.getDecoder().decode(ivPart)
        val cipherText = Base64.getDecoder().decode(bodyPart)
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(GCM_TAG_LENGTH_BITS, iv))
        }
        return String(cipher.doFinal(cipherText), Charsets.UTF_8)
    }
}
