package com.pappmichel.evetraderlocal.data.sde

import java.io.Reader
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request

const val FUZZWORK_CSV_BASE = "https://www.fuzzwork.co.uk/dump/latest/csv/"

private const val USER_AGENT = "eve-trader-local-android"

/** Any one file from the dump is a fine freshness proxy - Fuzzwork
 * regenerates the whole dump directory together, and `invTypes.csv` is
 * already the first file a refresh fetches. Same file, same reasoning, as
 * sde.py's `_FRESHNESS_FILE`. */
private const val FRESHNESS_FILE = "invTypes.csv"

class SdeDownloadError(message: String) : RuntimeException(message)

/** Fetches Fuzzwork's republished SDE CSVs.
 *
 * Why Fuzzwork at all: ESI does not expose the static data this cache holds
 * in bulk - resolving 50k type names through `/universe/types/` one call at a
 * time is the ~2000-call walk CandidateDiscovery already has to do, and that
 * is the cost this cache exists to remove. Fuzzwork (fuzzwork.co.uk)
 * republishes CCP's Static Data Export as CSV after every patch, which is
 * exactly what the desktop build downloads too (sde.py) - the same third
 * party, the same URLs, so both builds see the same snapshot of the same
 * data.
 *
 * Retry/backoff mirrors EsiClient's `getBody`, adapted to a very different
 * kind of request: these are large, uncacheable, plain-CSV bodies from a
 * static file host rather than small JSON responses from a rate-limited API,
 * so there is no 420/429 `Retry-After` handling to do (Fuzzwork does not rate
 * limit static files) and no pagination. What remains is the part that
 * matters for a six-file sequential download: one transient blip must not
 * abort the whole ~19MB refresh, which is exactly the failure sde.py's own
 * `_fetch_csv` retry loop was added to fix. Three attempts, growing backoff,
 * matching that loop's `time.sleep(attempt * 2)`.
 *
 * Parsing happens *inside* the retry loop, via the `parse` callback: the body
 * is streamed and projected into rows as it arrives (see SdeCsv for why it is
 * never held as one big String), so a failure halfway through attempt 1 has
 * already emitted rows. Handing the caller a `parse` function instead of a
 * Reader means each attempt starts from an empty accumulator of the caller's
 * own making, and a retry can never splice half of one attempt onto all of
 * the next. */
class SdeDownloader(
    private val http: OkHttpClient = OkHttpClient(),
    private val baseUrl: String = FUZZWORK_CSV_BASE,
) {
    /** Streams `<baseUrl><filename>` through `parse`, retrying the whole
     * fetch-and-parse on any network failure or non-2xx response.
     *
     * `parse` runs on the IO dispatcher while the response body is open, and
     * must consume the reader before returning - the body is closed as soon
     * as it does. */
    suspend fun <T> fetchCsv(filename: String, retries: Int = 3, parse: (Reader) -> T): T =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$baseUrl$filename")
                .header("User-Agent", USER_AGENT)
                .build()

            var lastError: String? = null
            for (attempt in 1..retries) {
                try {
                    http.newCall(request).execute().use { response ->
                        if (!response.isSuccessful) {
                            lastError = "HTTP ${response.code} for ${request.url}"
                            return@use
                        }
                        // charStream() decodes using the response's own charset
                        // (UTF-8 for these files); the BOM Fuzzwork prefixes
                        // them with is stripped by SdeCsv, the counterpart of
                        // sde.py's decode("utf-8-sig").
                        return@withContext parse(response.body!!.charStream())
                    }
                } catch (e: Exception) {
                    lastError = e.message ?: e.toString()
                }
                if (attempt < retries) delay(attempt * 2000L)
            }
            throw SdeDownloadError(
                "Could not download $filename from Fuzzwork after $retries attempts: $lastError"
            )
        }

    /** The dump's current ETag from a plain HEAD request - no CSV body
     * downloaded - or null on any failure.
     *
     * Best-effort by design, exactly like sde.py's `_dump_etag`: a third
     * party's freshness metadata being briefly unavailable must not block a
     * real refresh, and a staleness check that cannot reach the server should
     * report "unknown" rather than an error. */
    suspend fun dumpEtag(): String? = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$baseUrl$FRESHNESS_FILE")
            .head()
            .header("User-Agent", USER_AGENT)
            .build()
        try {
            http.newCall(request).execute().use { response ->
                if (response.isSuccessful) response.header("ETag") else null
            }
        } catch (e: Exception) {
            null
        }
    }
}
