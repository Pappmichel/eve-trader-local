package com.pappmichel.evetraderlocal.data.auth

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ports the desktop build's `tests/test_auth.py`'s
 * `test_is_expired_respects_skew` - `TokenRecord.isExpired` is pure
 * (no network, no Android dependency), unlike the rest of this package. */
class TokenRecordTest {
    private fun aRecord(expiresAt: Long) = TokenRecord(
        role = "seller:1", characterId = 1, characterName = "Test",
        accessToken = "a", refreshToken = "r", expiresAt = expiresAt, scopes = "",
    )

    @Test
    fun `is expired respects skew`() {
        val nowSeconds = System.currentTimeMillis() / 1000
        assertFalse(aRecord(expiresAt = nowSeconds + 3600).isExpired())
        assertTrue(aRecord(expiresAt = nowSeconds - 1).isExpired())
        assertTrue(aRecord(expiresAt = nowSeconds + 30).isExpired(skewSeconds = 60))
    }
}
