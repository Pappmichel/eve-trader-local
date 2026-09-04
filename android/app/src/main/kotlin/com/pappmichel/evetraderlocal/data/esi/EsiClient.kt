package com.pappmichel.evetraderlocal.data.esi

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response

const val ESI_BASE = "https://esi.evetech.net/latest"
private const val USER_AGENT = "eve-trader-local-android (contact: set EVE_CONTACT_EMAIL)"
private const val METALEVEL_ATTRIBUTE_ID = 633 // EVE SDE dogma attribute "metaLevel" (0=Tech I, 5=Tech II, ...)

class EsiError(message: String) : RuntimeException(message)

@Serializable
data class MarketGroupResponse(
    val name: String = "",
    @SerialName("parent_group_id") val parentGroupId: Int? = null,
    val types: List<Int> = emptyList(),
)

@Serializable
data class DogmaAttribute(
    @SerialName("attribute_id") val attributeId: Int,
    val value: Double,
)

@Serializable
data class TypeInfoResponse(
    val name: String = "",
    val volume: Double? = null,
    @SerialName("packaged_volume") val packagedVolume: Double? = null,
    @SerialName("dogma_attributes") val dogmaAttributes: List<DogmaAttribute> = emptyList(),
)

fun TypeInfoResponse.metaLevel(): Int? =
    dogmaAttributes.firstOrNull { it.attributeId == METALEVEL_ATTRIBUTE_ID }?.value?.toInt()

/** Thin wrapper around EVE Online's public ESI endpoints needed by
 * candidate discovery - the direct counterpart of the desktop build's
 * esi_client.py, ported only as far as Trading's first vertical slice
 * needs so far (see ROADMAP.md's Android section). Retry/backoff mirrors
 * esi_client.py's `_get_response`: 420/429 (rate-limited) backs off and
 * retries, 5xx backs off and retries, anything else raises immediately. */
class EsiClient(private val http: OkHttpClient = OkHttpClient()) {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun listMarketGroupIds(): List<Int> =
        json.decodeFromString(getBody("/markets/groups/", mapOf("datasource" to "tranquility")))

    suspend fun getMarketGroup(groupId: Int): MarketGroupResponse =
        json.decodeFromString(
            getBody("/markets/groups/$groupId/", mapOf("datasource" to "tranquility", "language" to "en"))
        )

    suspend fun getTypeInfo(typeId: Int): TypeInfoResponse =
        json.decodeFromString(
            getBody("/universe/types/$typeId/", mapOf("datasource" to "tranquility", "language" to "en"))
        )

    private suspend fun getBody(path: String, params: Map<String, String>, retries: Int = 3): String =
        withContext(Dispatchers.IO) {
            val urlBuilder = "$ESI_BASE$path".toHttpUrl().newBuilder()
            params.forEach { (key, value) -> urlBuilder.addQueryParameter(key, value) }
            val request = Request.Builder().url(urlBuilder.build()).header("User-Agent", USER_AGENT).build()

            var lastError: String? = null
            for (attempt in 1..retries) {
                val response: Response = http.newCall(request).execute()
                response.use {
                    if (it.isSuccessful) {
                        return@withContext it.body!!.string()
                    }
                    lastError = "HTTP ${it.code} for ${request.url}"
                    if (it.code == 420 || it.code == 429) {
                        delay(retryAfterMillis(it, attempt))
                        return@use
                    }
                    if (it.code in 500..504 && attempt < retries) {
                        delay((attempt * 1500).toLong())
                        return@use
                    }
                    throw EsiError(lastError!!)
                }
            }
            throw EsiError(lastError ?: "Exhausted retries for $path")
        }

    private fun retryAfterMillis(response: Response, attempt: Int): Long {
        val header = response.header("Retry-After")?.toDoubleOrNull()
        if (header != null) return (header * 1000).toLong()
        return minOf(1000L shl attempt, 20_000L)
    }
}
