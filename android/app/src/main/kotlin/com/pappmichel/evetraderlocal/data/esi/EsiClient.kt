package com.pappmichel.evetraderlocal.data.esi

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
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

/** Jita, the solar system every "buy in Jita, sell at the structure" figure
 * in this app is written against. Lives here, next to the endpoints that
 * take it, so there is exactly one definition of it for every caller. */
const val JITA_SOLAR_SYSTEM_ID = 30_000_142

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

@Serializable
data class MarketOrder(
    @SerialName("order_id") val orderId: Long = 0,
    @SerialName("type_id") val typeId: Int = 0,
    val price: Double = 0.0,
    @SerialName("is_buy_order") val isBuyOrder: Boolean = false,
    @SerialName("volume_remain") val volumeRemain: Double = 0.0,
    /** Absent from every current caller of this type until Station
     * Trading's undercut check, which needs to narrow a region's full
     * order book down to one specific (Jita trade hub) station - see
     * StationTradingUndercut.kt. */
    @SerialName("location_id") val locationId: Long = 0,
)

@Serializable
data class CharacterOrder(
    @SerialName("order_id") val orderId: Long = 0,
    @SerialName("type_id") val typeId: Int = 0,
    val price: Double = 0.0,
    @SerialName("is_buy_order") val isBuyOrder: Boolean = false,
    @SerialName("volume_remain") val volumeRemain: Double = 0.0,
    @SerialName("location_id") val locationId: Long = 0,
    @SerialName("region_id") val regionId: Int = 0,
)

@Serializable
data class CharacterSkill(
    @SerialName("skill_id") val skillId: Int = 0,
    @SerialName("active_skill_level") val activeSkillLevel: Int = 0,
)

@Serializable
data class CharacterSkillsResponse(
    val skills: List<CharacterSkill> = emptyList(),
)

@Serializable
data class CharacterAsset(
    @SerialName("type_id") val typeId: Int = 0,
    @SerialName("location_id") val locationId: Long = 0,
    val quantity: Double = 0.0,
    @SerialName("location_flag") val locationFlag: String = "",
)

@Serializable
data class SolarSystemResponse(
    val stations: List<Long> = emptyList(),
)

/** One ESI contract, exactly the fields Doctrine's Contract History screen
 * needs - the counterpart of esi_client.py's `character_contracts` raw dict
 * (that Python method returns ESI's JSON verbatim; this is the same schema
 * as a typed data class). `type`/`status` are ESI's own string vocabularies
 * ("item_exchange"/"auction"/"courier"/...; "outstanding"/"in_progress"/
 * "finished_issuer"/"finished_contractor"/"finished"/"cancelled"/"rejected"/
 * "failed"/"deleted"/"reversed") - kept as raw strings here, not a Kotlin
 * enum, since this screen only ever filters/displays them, never branches
 * exhaustively on every case the way a `when` would want to. */
@Serializable
data class EsiContract(
    @SerialName("contract_id") val contractId: Long = 0,
    val type: String = "",
    val status: String = "",
    @SerialName("title") val title: String? = null,
    @SerialName("for_corporation") val forCorporation: Boolean = false,
    @SerialName("issuer_id") val issuerId: Long = 0,
    @SerialName("acceptor_id") val acceptorId: Long? = null,
    @SerialName("start_location_id") val startLocationId: Long? = null,
    val price: Double? = null,
    @SerialName("date_issued") val dateIssued: String = "",
    @SerialName("date_expired") val dateExpired: String? = null,
    @SerialName("date_completed") val dateCompleted: String? = null,
)

/** One line of a contract's item list - the counterpart of esi_client.py's
 * `character_contract_items` raw dict. */
@Serializable
data class EsiContractItem(
    @SerialName("record_id") val recordId: Long = 0,
    @SerialName("type_id") val typeId: Int = 0,
    val quantity: Long = 0,
    @SerialName("is_included") val isIncluded: Boolean = true,
    @SerialName("is_singleton") val isSingleton: Boolean = false,
)

/** One row of `/characters/{character_id}/industry/jobs/` - the counterpart
 * of esi_client.py's own industry-jobs read (see production/esi_sync.py).
 * `productTypeId`/`blueprintTypeId` are null for a job with no direct
 * product (research/copying jobs output a BPO/BPC, not a market item, same
 * as the desktop build's own `product_type_id IS NULL` case). `installerId`
 * is the *character* who started the job - for a corp job that can be a
 * different character than the one whose token fetched this list, which is
 * exactly why `ProductionJobs.kt` resolves it against every logged-in
 * producer character's own name rather than assuming it's always the
 * caller. */
@Serializable
data class IndustryJob(
    @SerialName("job_id") val jobId: Long = 0,
    @SerialName("installer_id") val installerId: Long = 0,
    @SerialName("activity_id") val activityId: Int = 0,
    @SerialName("blueprint_type_id") val blueprintTypeId: Int = 0,
    @SerialName("product_type_id") val productTypeId: Int? = null,
    val runs: Int = 0,
    val status: String = "",
    @SerialName("start_date") val startDate: String = "",
    @SerialName("end_date") val endDate: String = "",
)

/** One row of `/characters/{character_id}/blueprints/` - the counterpart of
 * esi_client.py's own blueprints read (see production/esi_sync.py's
 * blueprint sync, and `OwnedBlueprints.kt`). `runs` is ESI's own "-1 means
 * this is a BPO, not a copy" sentinel (a BPO has infinite runs so ESI never
 * reports a real count for one) - **not** `quantity`, which is a *stack*
 * count only when positive; for a blueprint item `quantity` itself is
 * usually another ESI sentinel (-1 original / -2 copy) rather than a real
 * stack size, exactly as `production/engine.py`'s own
 * `list_owned_blueprints` documents and `OwnedBlueprints.kt` mirrors.
 * `locationId` is left unresolved here (no structure-name lookup) - this
 * port aggregates across every location the same way the desktop view's
 * own `storage.load_owned_blueprints()` read does, not a location breakdown. */
@Serializable
data class CharacterBlueprint(
    @SerialName("item_id") val itemId: Long = 0,
    @SerialName("type_id") val typeId: Int = 0,
    @SerialName("location_id") val locationId: Long = 0,
    val quantity: Long = 0,
    @SerialName("time_efficiency") val timeEfficiency: Int = 0,
    @SerialName("material_efficiency") val materialEfficiency: Int = 0,
    val runs: Int = 0,
)

@Serializable
data class CharacterWalletTransaction(
    @SerialName("transaction_id") val transactionId: Long,
    @SerialName("type_id") val typeId: Int,
    @SerialName("is_buy") val isBuy: Boolean = false,
    val quantity: Long = 0,
    @SerialName("unit_price") val unitPrice: Double = 0.0,
    val date: String = "",
    @SerialName("location_id") val locationId: Long = 0,
    /** Links 1:1 to the wallet *journal* entry recording this same trade,
     * whose `amount` is the real ISK moved after sales tax - see
     * TradeReconciliation.kt's use of it. */
    @SerialName("journal_ref_id") val journalRefId: Long? = null,
)

@Serializable
data class CharacterWalletJournalEntry(
    val id: Long,
    val amount: Double = 0.0,
    @SerialName("ref_type") val refType: String = "",
    val date: String = "",
)

/** Summary stats for one side (buy/sell) of an order book - a robust price
 * percentile plus total listed volume, see esi_client.py's own `OrderStats`
 * and `_summarize_orders`. */
data class OrderStats(
    val sellPercentile: Double?,
    val sellVolume: Double,
    val buyPercentile: Double?,
    val buyVolume: Double,
)

/** Nearest-rank percentile, matching esi_client.py's `_percentile`:
 * `sorted` must already be sorted ascending for the caller's own side
 * (sells ascending for a low percentile, buys descending so the same `pct`
 * picks from the top) - not re-sorted here. */
// internal (not private) so EsiClientTest.kt can exercise these directly,
// the same way esi_client.py's own test file imports its underscore-
// prefixed _percentile/_summarize_orders straight in.
internal fun percentile(sorted: List<Double>, pct: Double): Double? {
    if (sorted.isEmpty()) return null
    val idx = minOf((sorted.size * pct).toInt(), sorted.size - 1)
    return sorted[idx]
}

internal fun summarizeOrders(orders: List<MarketOrder>): OrderStats {
    val sells = orders.filter { !it.isBuyOrder }.map { it.price }.sorted()
    val buys = orders.filter { it.isBuyOrder }.map { it.price }.sortedDescending()
    return OrderStats(
        sellPercentile = percentile(sells, 0.05),
        sellVolume = orders.filter { !it.isBuyOrder }.sumOf { it.volumeRemain },
        buyPercentile = percentile(buys, 0.05),
        buyVolume = orders.filter { it.isBuyOrder }.sumOf { it.volumeRemain },
    )
}

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

    /** Regional order-book stats for one type_id - the counterpart of
     * esi_client.py's `region_order_stats` (public endpoint, no token
     * needed). Paginated defensively like the desktop build's
     * `region_orders_raw`: a single type_id in a normal region is very
     * unlikely to exceed one page, but nothing in the ESI spec guarantees
     * that. */
    suspend fun regionOrderStats(regionId: Int, typeId: Int): OrderStats {
        val out = mutableListOf<MarketOrder>()
        var page = 1
        while (true) {
            val (body, totalPages) = getBodyWithPages(
                "/markets/$regionId/orders/",
                mapOf(
                    "datasource" to "tranquility", "order_type" to "all",
                    "type_id" to typeId.toString(), "page" to page.toString(),
                ),
            )
            val chunk: List<MarketOrder> = json.decodeFromString(body)
            if (chunk.isEmpty()) break
            out.addAll(chunk)
            if (page >= totalPages) break
            page++
        }
        return summarizeOrders(out)
    }

    /** The region's full, unsummarized order book for one type_id - the
     * counterpart of esi_client.py's `region_orders_raw`. Public endpoint,
     * no token needed. Station Trading's undercut check needs the raw
     * per-order list (to cross-reference by order_id against the trader's
     * own orders), not the summarized percentile/volume `regionOrderStats`
     * above returns. */
    suspend fun regionOrdersRaw(regionId: Int, typeId: Int): List<MarketOrder> {
        val out = mutableListOf<MarketOrder>()
        var page = 1
        while (true) {
            val (body, totalPages) = getBodyWithPages(
                "/markets/$regionId/orders/",
                mapOf(
                    "datasource" to "tranquility", "order_type" to "all",
                    "type_id" to typeId.toString(), "page" to page.toString(),
                ),
            )
            val chunk: List<MarketOrder> = json.decodeFromString(body)
            if (chunk.isEmpty()) break
            out.addAll(chunk)
            if (page >= totalPages) break
            page++
        }
        return out
    }

    /** Same as regionOrderStats, but for many type_ids at once - one ESI
     * call per type_id (no regional batch endpoint), run concurrently the
     * same way esi_client.py's `region_order_stats_bulk` uses a thread
     * pool. A failed lookup for one type_id doesn't affect the others. */
    suspend fun regionOrderStatsBulk(regionId: Int, typeIds: List<Int>): Map<Int, OrderStats> =
        withContext(Dispatchers.IO) {
            typeIds.map { tid -> async { tid to runCatching { regionOrderStats(regionId, tid) }.getOrNull() } }
                .awaitAll()
                .mapNotNull { (tid, stats) -> stats?.let { tid to it } }
                .toMap()
        }

    /** The structure's full, unsummarized order book - requires a token with
     * esi-markets.structure_markets.v1 for a character docked at / with
     * access to that structure. Counterpart of esi_client.py's
     * `structure_orders_raw`; ESI has no type_id filter on this endpoint, so
     * every call downloads the whole book. */
    suspend fun structureOrdersRaw(structureId: Long, accessToken: String): List<MarketOrder> {
        val out = mutableListOf<MarketOrder>()
        var page = 1
        while (true) {
            val (body, totalPages) = getBodyWithPages(
                "/markets/structures/$structureId/",
                mapOf("datasource" to "tranquility", "page" to page.toString()),
                accessToken,
            )
            val chunk: List<MarketOrder> = json.decodeFromString(body)
            if (chunk.isEmpty()) break
            out.addAll(chunk)
            if (page >= totalPages) break
            page++
        }
        return out
    }

    /** Groups one full structure order-book download by type_id, the same
     * "download once, group locally" shape as esi_client.py's
     * `structure_order_stats_bulk` (that endpoint has no type_id filter). */
    suspend fun structureOrderStatsBulk(
        structureId: Long, typeIds: List<Int>, accessToken: String,
    ): Map<Int, OrderStats> {
        val orders = structureOrdersRaw(structureId, accessToken)
        val byType = orders.groupBy { it.typeId }
        return typeIds.associateWith { tid -> summarizeOrders(byType[tid] ?: emptyList()) }
    }

    /** This character's current ISK wallet balance - a bare number, not a
     * list/object (ESI's own response shape for this endpoint). Same
     * esi-wallet.read_character_wallet.v1 scope every login role already
     * requests. Counterpart of esi_client.py's `character_wallet_balance`. */
    suspend fun characterWalletBalance(characterId: Long, accessToken: String): Double =
        getBody("/characters/$characterId/wallet/", mapOf("datasource" to "tranquility"), accessToken).toDouble()

    /** This character's open market orders (buy and sell) - the counterpart
     * of esi_client.py's `character_orders`. Requires a token with
     * esi-markets.read_character_orders.v1. Not paginated: ESI's own
     * endpoint isn't (a character's order count is small and bounded),
     * same as the desktop build's own single-`_get` implementation. */
    suspend fun characterOrders(characterId: Long, accessToken: String): List<CharacterOrder> =
        json.decodeFromString(
            getBodyWithPages(
                "/characters/$characterId/orders/", mapOf("datasource" to "tranquility"), accessToken,
            ).first
        )

    /** This character's assets (every item they own, wherever it is) - the
     * counterpart of esi_client.py's `character_assets`. Requires a token
     * with esi-assets.read_assets.v1. Paginated: a well-stocked character's
     * asset list is unbounded, unlike their order list. */
    suspend fun characterAssets(characterId: Long, accessToken: String): List<CharacterAsset> {
        val out = mutableListOf<CharacterAsset>()
        var page = 1
        while (true) {
            val (body, totalPages) = getBodyWithPages(
                "/characters/$characterId/assets/",
                mapOf("datasource" to "tranquility", "page" to page.toString()),
                accessToken,
            )
            val chunk: List<CharacterAsset> = json.decodeFromString(body)
            if (chunk.isEmpty()) break
            out.addAll(chunk)
            if (page >= totalPages) break
            page++
        }
        return out
    }

    /** This character's wallet transaction history. Returns up to
     * WALLET_TRANSACTIONS_PAGE_SIZE (2500, ESI's fixed per-call cap for this
     * endpoint) transactions, most recent first. Cursor pagination via
     * `fromId` (pass the oldest transaction_id from a previous call to page
     * further back in time), not the page/X-Pages scheme the other paged
     * methods here use - a caller loops it until it has covered the
     * lookback window it wants (see TradeReconciliation.fetchRecentTransactions).
     * Requires esi-wallet.read_character_wallet.v1. */
    suspend fun characterWalletTransactions(
        characterId: Long,
        accessToken: String,
        fromId: Long? = null,
    ): List<CharacterWalletTransaction> {
        val params = mutableMapOf("datasource" to "tranquility")
        if (fromId != null) params["from_id"] = fromId.toString()
        return json.decodeFromString(getBody("/characters/$characterId/wallet/transactions/", params, accessToken))
    }

    /** This character's wallet journal (every ISK-moving event, not just
     * market trades - contract payments, bounties, taxes, ...). Standard
     * page/X-Pages pagination, same as characterOrders/characterAssets
     * above. The point of it for trade reconciliation: a transaction's own
     * `journal_ref_id` links 1:1 to the journal entry recording that same
     * sale (`ref_type: "market_transaction"`), whose `amount` is the real
     * ISK credited *after* sales tax. Same esi-wallet.read_character_
     * wallet.v1 scope, no extra grant. */
    suspend fun characterWalletJournal(characterId: Long, accessToken: String): List<CharacterWalletJournalEntry> {
        val out = mutableListOf<CharacterWalletJournalEntry>()
        var page = 1
        while (true) {
            val (body, totalPages) = getBodyWithPages(
                "/characters/$characterId/wallet/journal/",
                mapOf("datasource" to "tranquility", "page" to page.toString()),
                accessToken,
            )
            val chunk: List<CharacterWalletJournalEntry> = json.decodeFromString(body)
            if (chunk.isEmpty()) break
            out.addAll(chunk)
            if (page >= totalPages) break
            page++
        }
        return out
    }

    /** This character's trained skill levels (active, not just trained -
     * matches esi_client.py's own `active_skill_level` read). Requires
     * esi-skills.read_skills.v1. Used by Station Trading to derive its
     * order-slot count (constants.py's `order_slots_from_skills`). */
    suspend fun characterSkills(characterId: Long, accessToken: String): List<CharacterSkill> =
        json.decodeFromString<CharacterSkillsResponse>(
            getBody("/characters/$characterId/skills/", mapOf("datasource" to "tranquility"), accessToken)
        ).skills

    /** This character's active/paused/ready/delivered industry jobs
     * (character *and* corp jobs the character installed, or - if they hold
     * the right corp role - every corp job regardless of installer; ESI
     * itself decides that, not this method) - the counterpart of
     * esi_client.py's own industry-jobs pull behind production/esi_sync.py.
     * Requires esi-industry.read_character_jobs.v1. `includeCompleted`
     * mirrors ESI's own query parameter of the same name; Current Jobs &
     * Slots only ever wants the still-running set, so it defaults to false
     * (ESI's own endpoint default). Paginated defensively like every other
     * list endpoint here, though a single character's active job count in
     * practice never approaches a second page. */
    suspend fun characterIndustryJobs(
        characterId: Long,
        accessToken: String,
        includeCompleted: Boolean = false,
    ): List<IndustryJob> {
        val out = mutableListOf<IndustryJob>()
        var page = 1
        while (true) {
            val (body, totalPages) = getBodyWithPages(
                "/characters/$characterId/industry/jobs/",
                mapOf(
                    "datasource" to "tranquility",
                    "include_completed" to includeCompleted.toString(),
                    "page" to page.toString(),
                ),
                accessToken,
            )
            val chunk: List<IndustryJob> = json.decodeFromString(body)
            if (chunk.isEmpty()) break
            out.addAll(chunk)
            if (page >= totalPages) break
            page++
        }
        return out
    }

    /** This character's owned blueprints (BPOs and BPCs, character *and*
     * corp-hangar items the character can see - ESI itself decides which,
     * not this method) - the counterpart of esi_client.py's own blueprints
     * pull behind production/esi_sync.py's blueprint sync. Requires
     * esi-characters.read_blueprints.v1. Paginated defensively like every
     * other list endpoint here, though a well-stocked character's owned
     * blueprint count rarely reaches a second page. */
    suspend fun characterBlueprints(characterId: Long, accessToken: String): List<CharacterBlueprint> {
        val out = mutableListOf<CharacterBlueprint>()
        var page = 1
        while (true) {
            val (body, totalPages) = getBodyWithPages(
                "/characters/$characterId/blueprints/",
                mapOf("datasource" to "tranquility", "page" to page.toString()),
                accessToken,
            )
            val chunk: List<CharacterBlueprint> = json.decodeFromString(body)
            if (chunk.isEmpty()) break
            out.addAll(chunk)
            if (page >= totalPages) break
            page++
        }
        return out
    }

    /** The NPC station ids in one solar system - a public endpoint, so this
     * needs no token. Used to find "is this in Jita" the way the desktop
     * build's own `storage.get_station_ids_in_system` does from its local
     * SDE cache (`fetch_buyer_already_covered`'s station check) - no SDE
     * cache exists on this platform yet (see ROADMAP.md's Android
     * section), but ESI itself already carries this for any system with
     * NPC stations, Jita included, so no cache is actually needed here. */
    suspend fun solarSystemStationIds(solarSystemId: Int): List<Long> =
        json.decodeFromString<SolarSystemResponse>(
            getBody("/universe/systems/$solarSystemId/", mapOf("datasource" to "tranquility", "language" to "en"))
        ).stations

    /** This character's contracts (issuer, acceptor, or assignee of) - the
     * counterpart of esi_client.py's `character_contracts`. Requires a token
     * with esi-contracts.read_character_contracts.v1. Paginated like
     * characterAssets; ESI itself only retains up to 30 days of finished
     * contracts (or any that are still outstanding/in_progress) - not
     * filtered further here, same as the desktop method's own docstring. */
    suspend fun characterContracts(characterId: Long, accessToken: String): List<EsiContract> {
        val out = mutableListOf<EsiContract>()
        var page = 1
        while (true) {
            val (body, totalPages) = getBodyWithPages(
                "/characters/$characterId/contracts/",
                mapOf("datasource" to "tranquility", "page" to page.toString()),
                accessToken,
            )
            val chunk: List<EsiContract> = json.decodeFromString(body)
            if (chunk.isEmpty()) break
            out.addAll(chunk)
            if (page >= totalPages) break
            page++
        }
        return out
    }

    /** One contract's item list - the counterpart of esi_client.py's
     * `character_contract_items`. Same esi-contracts.read_character_
     * contracts.v1 scope as [characterContracts], not a separate grant. Not
     * paginated: ESI's own endpoint for this isn't (a single contract's item
     * count is small and bounded), same as the desktop method's own
     * single-`_get` call. */
    suspend fun characterContractItems(characterId: Long, contractId: Long, accessToken: String): List<EsiContractItem> =
        json.decodeFromString(
            getBody(
                "/characters/$characterId/contracts/$contractId/items/",
                mapOf("datasource" to "tranquility"), accessToken,
            )
        )

    private suspend fun getBodyWithPages(
        path: String, params: Map<String, String>, accessToken: String? = null, retries: Int = 3,
    ): Pair<String, Int> = withContext(Dispatchers.IO) {
        val urlBuilder = "$ESI_BASE$path".toHttpUrl().newBuilder()
        params.forEach { (key, value) -> urlBuilder.addQueryParameter(key, value) }
        val requestBuilder = Request.Builder().url(urlBuilder.build()).header("User-Agent", USER_AGENT)
        if (accessToken != null) requestBuilder.header("Authorization", "Bearer $accessToken")
        val request = requestBuilder.build()

        var lastError: String? = null
        for (attempt in 1..retries) {
            val response: Response = http.newCall(request).execute()
            response.use {
                if (it.isSuccessful) {
                    val pages = it.header("X-Pages")?.toIntOrNull() ?: 1
                    return@withContext it.body!!.string() to pages
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

    private suspend fun getBody(
        path: String, params: Map<String, String>, accessToken: String? = null, retries: Int = 3,
    ): String = getBodyWithPages(path, params, accessToken, retries).first

    private fun retryAfterMillis(response: Response, attempt: Int): Long {
        val header = response.header("Retry-After")?.toDoubleOrNull()
        if (header != null) return (header * 1000).toLong()
        return minOf(1000L shl attempt, 20_000L)
    }
}
