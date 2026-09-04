package com.pappmichel.evetraderlocal.data.history

import android.util.Xml
import java.io.StringReader
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.xmlpull.v1.XmlPullParser

const val GOONMETRICS_HISTORY_BASE = "https://goonmetrics.apps.gnf.lt/api/price_history/"
private const val USER_AGENT = "eve-trader-local-android (contact: set EVE_CONTACT_EMAIL)"

// Same default as the desktop build's config.py `TradingConfig.chunk_size`
// (type_ids per Goonmetrics price-history request). Not a TradingConfig
// field on this platform yet: nothing else on Android has a reason to tune
// it, and TradingConfig here deliberately only carries the fields a ported
// screen actually reads (see its own docstring).
const val HISTORY_CHUNK_SIZE = 25

// Chunks are independent requests, so they're fetched concurrently -
// `price_history_chunked`'s own `max_workers=6` thread pool, expressed the
// way CandidateDiscovery.kt already expresses bounded fan-out on this
// platform (a Semaphore-bounded set of coroutines, never an unbounded
// `async` per chunk against a third-party no-SLA host).
private const val HISTORY_CONCURRENCY = 6

class GoonmetricsError(message: String) : RuntimeException(message)

/** One day of region price history for one type - the same fields as the
 * desktop build's goonmetrics_client.py `HistoryPoint` dataclass. */
data class HistoryPoint(
    val regionId: Int,
    val typeId: Int,
    val date: String,
    val minPrice: Double,
    val maxPrice: Double,
    val avgPrice: Double,
    val movement: Double,
    val numOrders: Int,
)

/** Client for gnf.lt's rehosting of the "Goonmetrics" community price-history
 * API - the price_history half of the desktop build's
 * goonmetrics_client.py, and only that half.
 *
 * Kept as its own client rather than folded into EsiClient.kt for the same
 * reason the desktop build keeps the two files apart: a different host, a
 * different (XML, not JSON) response format, and a third-party no-SLA
 * server whose failure modes have nothing to do with ESI's. The
 * `current_prices` half of the desktop client is deliberately *not* ported -
 * that one is the failsafe behind a structure order-book lookup, a
 * different feature nothing on Android reads yet.
 *
 * Response shape (parsed with Android's built-in XmlPullParser - no new
 * Gradle dependency needed for XML, and the whole document is a flat
 * attribute-only tree):
 *
 *     <evec_api><result><rowset name="history">
 *       <type id="...">
 *         <history date="..." avgPrice="..." maxPrice="..." minPrice="..."
 *                  movement="..." numOrders="..."/>
 *       </type>
 *     </rowset></result></evec_api>
 *
 * Deliberate omission vs. desktop: no ESI-history fallback. The desktop
 * `price_history` silently falls back to ESI's per-type
 * /markets/{region}/history/ when Goonmetrics is unreachable, because there
 * it sits inside a long unattended candidate search that shouldn't die on a
 * third-party outage. Here the only caller is a user-pressed "Refresh" on
 * one screen, where a failure is immediately visible and immediately
 * retryable - so a fetch failure just surfaces as a caught exception in the
 * screen's status line, the same way every other live-fetch screen on this
 * platform already handles a network failure. Worth adding later if this
 * client picks up an unattended caller.
 *
 * Retry/backoff mirrors EsiClient.kt's `getBody` (which in turn mirrors
 * esi_client.py's `_get_response`): 429 and 5xx back off and retry,
 * anything else raises immediately. */
class GoonmetricsClient(
    private val http: OkHttpClient = OkHttpClient(),
    private val historyBase: String = GOONMETRICS_HISTORY_BASE,
) {
    /** Daily history for a batch of type_ids in one region, in one request -
     * the endpoint takes a comma-separated type_id list, which is why
     * chunking (below) is about request size, not about one call per id. */
    suspend fun priceHistory(regionId: Int, typeIds: List<Int>): List<HistoryPoint> {
        if (typeIds.isEmpty()) return emptyList()
        val body = getBody(
            mapOf("region_id" to regionId.toString(), "type_id" to typeIds.joinToString(",")),
        )
        return parseHistoryXml(body, regionId)
    }

    /** Splits `typeIds` into `chunkSize` batches and fetches them
     * concurrently - the direct counterpart of `price_history_chunked`.
     *
     * Each chunk is isolated in its own try/catch, same as the desktop
     * version's per-chunk try/except: one chunk failing (an outage that
     * outlasts the retries, a malformed response) loses that chunk's points
     * instead of the whole refresh. A caller that gets *nothing* back sees
     * that as an empty result, not as an exception - the screen's
     * "no item has enough paired history yet" message covers that case
     * honestly either way. */
    suspend fun priceHistoryChunked(
        regionId: Int,
        typeIds: List<Int>,
        chunkSize: Int = HISTORY_CHUNK_SIZE,
    ): List<HistoryPoint> = coroutineScope {
        val semaphore = Semaphore(HISTORY_CONCURRENCY)
        typeIds.chunked(chunkSize).map { chunk ->
            async(Dispatchers.IO) {
                semaphore.withPermit {
                    try {
                        priceHistory(regionId, chunk)
                    } catch (e: Exception) {
                        emptyList()
                    }
                }
            }
        }.flatMap { it.await() }
    }

    private suspend fun getBody(params: Map<String, String>, retries: Int = 3): String =
        withContext(Dispatchers.IO) {
            val urlBuilder = historyBase.toHttpUrl().newBuilder()
            params.forEach { (key, value) -> urlBuilder.addQueryParameter(key, value) }
            val request = Request.Builder().url(urlBuilder.build()).header("User-Agent", USER_AGENT).build()

            var lastError: String? = null
            for (attempt in 1..retries) {
                val response = http.newCall(request).execute()
                response.use {
                    if (it.isSuccessful) {
                        return@withContext it.body!!.string()
                    }
                    lastError = "HTTP ${it.code} for ${request.url}"
                    if (it.code == 429 || it.code in 500..504) {
                        if (attempt < retries) {
                            delay((attempt * 1500).toLong())
                            return@use
                        }
                    }
                    throw GoonmetricsError(lastError!!)
                }
            }
            throw GoonmetricsError(lastError ?: "Exhausted retries for price history")
        }
}

/** Pull-parses the history document into flat HistoryPoints, carrying
 * `regionId` in from the request (the response body doesn't repeat it) -
 * same as `_parse_history_xml`'s own `region_id` parameter.
 *
 * Any `<history>` row missing a numeric attribute is skipped rather than
 * failing the whole document: one malformed day shouldn't cost the caller
 * every other type_id in the same chunk. Top-level, not a method, so it can
 * be tested against a hand-written document with no client or network
 * involved (see GoonmetricsClientTest) - which is also why the parser
 * itself is a defaulted parameter: `android.util.Xml` is a framework stub
 * on a plain JVM unit-test classpath and throws if called, so the test
 * passes in a real XmlPullParser implementation instead of mocking the
 * framework. */
fun parseHistoryXml(xml: String, regionId: Int, parser: XmlPullParser = Xml.newPullParser()): List<HistoryPoint> {
    parser.setInput(StringReader(xml))

    val points = mutableListOf<HistoryPoint>()
    var typeId: Int? = null
    var event = parser.eventType
    while (event != XmlPullParser.END_DOCUMENT) {
        when (event) {
            XmlPullParser.START_TAG -> when (parser.name) {
                "type" -> typeId = parser.getAttributeValue(null, "id")?.toIntOrNull()
                "history" -> {
                    val point = historyPoint(parser, regionId, typeId)
                    if (point != null) points.add(point)
                }
            }
            XmlPullParser.END_TAG -> if (parser.name == "type") typeId = null
        }
        event = parser.next()
    }
    return points
}

private fun historyPoint(parser: XmlPullParser, regionId: Int, typeId: Int?): HistoryPoint? {
    if (typeId == null) return null
    val date = parser.getAttributeValue(null, "date") ?: return null
    val minPrice = parser.getAttributeValue(null, "minPrice")?.toDoubleOrNull() ?: return null
    val maxPrice = parser.getAttributeValue(null, "maxPrice")?.toDoubleOrNull() ?: return null
    val avgPrice = parser.getAttributeValue(null, "avgPrice")?.toDoubleOrNull() ?: return null
    val movement = parser.getAttributeValue(null, "movement")?.toDoubleOrNull() ?: return null
    val numOrders = parser.getAttributeValue(null, "numOrders")?.toIntOrNull() ?: return null
    return HistoryPoint(
        regionId = regionId, typeId = typeId, date = date, minPrice = minPrice,
        maxPrice = maxPrice, avgPrice = avgPrice, movement = movement, numOrders = numOrders,
    )
}
