package com.pappmichel.evetraderlocal.data.sde

import androidx.room.withTransaction
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.production.BlueprintForProduct
import com.pappmichel.evetraderlocal.data.production.CandidateType
import com.pappmichel.evetraderlocal.data.production.InventionRecipe
import com.pappmichel.evetraderlocal.data.production.InventionSdeSource
import com.pappmichel.evetraderlocal.data.production.ProductionCandidateSource
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** One SDE file this port downloads, and the table it feeds. Ordered as the
 * refresh fetches them, `invTypes.csv` first - both because it is the biggest
 * (so a refresh that is going to fail on a slow connection fails early rather
 * than after five successful files) and because it is the file the ETag
 * freshness check is taken from. */
enum class SdeFile(val filename: String, val table: String) {
    TYPES("invTypes.csv", "sde_types"),
    GROUPS("invGroups.csv", "sde_groups"),
    CATEGORIES("invCategories.csv", "sde_categories"),
    MARKET_GROUPS("invMarketGroups.csv", "sde_market_groups"),
    SOLAR_SYSTEMS("mapSolarSystems.csv", "sde_solar_systems"),
    STATIONS("staStations.csv", "sde_stations"),
    // Reprocessing material yields (Ore & Minerals / Reprocessing Quote -
    // see SdeTypeMaterialEntity's own docstring). Fetched last: every other
    // table already exists without it, so a Reprocessing-Quote-only failure
    // here should not make an otherwise-successful refresh look worse than
    // it is - see this enum's own file-ordering note above for why TYPES is
    // first for the opposite reason (it is the biggest, and the ETag file).
    TYPE_MATERIALS("invTypeMaterials.csv", "sde_type_materials"),
    // Manufacturing (activityID=1) blueprint BOM - see
    // SdeBlueprintMaterialEntity/SdeBlueprintProductEntity's own docstrings
    // for why Reaction/Copying are still out of scope. Added for
    // Production's first real build-cost view
    // (data/production/ProductionBuildCost.kt) - fetched near the end, same
    // "every other table already works without it" reasoning
    // TYPE_MATERIALS' own comment gives. Now also carries Invention
    // (activityID=8) rows, added for the Invention Estimator port - see
    // RELEVANT_ACTIVITY_IDS below.
    BLUEPRINT_PRODUCTS("industryActivityProducts.csv", "sde_blueprint_products"),
    BLUEPRINT_MATERIALS("industryActivityMaterials.csv", "sde_blueprint_materials"),
    // Invention (activityID=8) success probabilities - the one piece of
    // Invention Estimator data with no Manufacturing counterpart at all, so
    // it gets its own table rather than reusing one of the two above.
    // Fetched last for the same "every earlier table already works without
    // it" reasoning as everything else at the end of this enum.
    INVENTION_PROBABILITY("industryActivityProbabilities.csv", "sde_invention_probability"),
}

/** Manufacturing is CCP's `activityID` 1, Invention is 8 - the two
 * activities `BLUEPRINT_MATERIALS`/`BLUEPRINT_PRODUCTS` now carry (see
 * `SdeBlueprintMaterialEntity`'s docstring for why Invention joined
 * Manufacturing there for the Invention Estimator port). Reaction (11) and
 * Copying (5) rows are still filtered out at parse time, same as `sde.py`'s
 * own `_RELEVANT_ACTIVITIES` filter on desktop, just narrower still - no
 * ported consumer needs either of those two yet. */
private val RELEVANT_ACTIVITY_IDS = setOf(1, 8)

/** The activity id `SdeDao.inventionProduct`/`inventionMaterials`/
 * `inventionRecipeCandidates` filter to - CCP's Invention activity, the
 * same constant desktop's `production/constants.ACTIVITY_INVENTION` names. */
private const val INVENTION_ACTIVITY_ID = 8

/** What a completed refresh reports back: row counts per table (the
 * counterpart of storage.py's `sde_row_counts`) and when it happened. */
data class SdeRefreshResult(
    val refreshedAt: String,
    val rowCounts: Map<String, Int>,
) {
    val totalRows: Int get() = rowCounts.values.sum()
}

/** The answer to "is it worth spending ~19MB of mobile data right now" -
 * mirrors sde.py's `check_for_newer_sde` field for field. */
data class SdeStaleness(
    val localRefreshedAt: String?,
    val remoteCheckSucceeded: Boolean,
    val newerSdeAvailable: Boolean,
)

/** The Android port of sde.py: download Fuzzwork's SDE CSVs, parse them, and
 * replace the local cache wholesale.
 *
 * **Scope: eight of the desktop build's twelve tables.** `refresh_sde()`
 * downloads twelve CSVs because the desktop build also backs a future
 * Doctrine EFT parser (`dgmTypeEffects.csv`) and Tech II/Faction detection
 * (`invMetaTypes.csv`). Neither of those exists on Android yet - not the
 * screens, not the business logic - so fetching their tables here would be
 * extra download and storage nothing on this platform can read. This port
 * covers exactly the tables Android's *existing* features could use today:
 *  - `invTypes` / `invGroups` / `invCategories` / `invMarketGroups` - the
 *    candidate universe and its real category names, i.e. the SDE-accelerated
 *    path CandidateDiscovery currently cannot take (see that object's own
 *    docstring, which explains the same gap from the other side).
 *  - `staStations` + `mapSolarSystems` - "which stations are in region X",
 *    which the station table alone cannot answer: `staStations.csv` has a
 *    solar system id and no region id, so the region question is a join.
 *  - `invTypeMaterials` (+ `invTypes.portionSize`) - reprocessing yields, for
 *    the Ore & Minerals / Reprocessing Quote screen (see
 *    `data/refining/ReprocessingYield.kt`). Added after the fact, exactly as
 *    this docstring predicted: a CSV, an entity, and a Room version bump.
 *  - `industryActivityMaterials` / `industryActivityProducts`, Manufacturing
 *    (activityID=1) rows - for Production's build-cost view
 *    (`data/production/ProductionBuildCost.kt`).
 *  - The same two tables' Invention (activityID=8) rows, plus
 *    `industryActivityProbabilities` in full - for the Invention Estimator
 *    (`data/production/InventionEstimator.kt`). This is the one table pair
 *    that grew *in place* rather than being added fresh: see
 *    `SdeBlueprintMaterialEntity`/`SdeBlueprintProductEntity`'s own
 *    docstrings for why Invention's rows share the Manufacturing tables
 *    instead of getting their own, and every Manufacturing-only query below
 *    (`blueprintForProduct`, `blueprintMaterials`, `manufacturableTypes`) for
 *    why each needed an explicit `activityId = 1` the moment that happened.
 *    Reaction (11) and Copying (5) are still out of scope - no ported
 *    consumer needs either.
 *
 *    A correction, for the record: an earlier pass through this file's own
 *    docstring described `industryActivityProbabilities`/
 *    `industryActivitySkills` as data the Invention Estimator would need
 *    that "the Android cache doesn't carry" and treated that as a genuine
 *    blocker. That was wrong in its implication - both are plain Fuzzwork
 *    CSVs, fetched through the exact same pipeline every other table here
 *    already uses; nothing about them was unreachable, they just hadn't been
 *    added yet (now one has been - see below on the other).
 * This mirrors how every other part of this Android build has been ported:
 * the shape now, the rest when the tool that needs it arrives. Adding a table
 * later is a CSV, an entity, and a Room version bump - not a redesign.
 *
 * **`industryActivitySkills.csv` was deliberately not added**, despite being
 * named alongside `industryActivityProbabilities.csv` when this port was
 * scoped. Checked against the desktop build itself first: `grep -rn
 * industryActivitySkills` across `eve_trader_local/` and `tests/` returns
 * nothing at all. `sde.py` never fetches it, `storage.py` never reads it,
 * and `production/invention.py`'s real skill-bonus math
 * (`skill_multiplier`) reads three flat `ProductionConfig` fields
 * (`encryption_skill_level`/`datacore_skill_1_level`/`datacore_skill_2_
 * level`) the user sets once, not a per-blueprint skill requirement looked
 * up from the SDE. Desktop Invention simply has no consumer for this table
 * either - so porting it here would be exactly the "several extra megabytes
 * of download and a table nothing can read" this docstring already argues
 * against for `invMetaTypes`/`dgmTypeEffects`, just for a table the *parent
 * feature itself* doesn't use.
 *
 * `metaGroupID` is still skipped inside `invTypes` - it needs the separate
 * `invMetaTypes.csv` fetch that only Tech II/Faction detection would read.
 *
 * **Atomicity.** Every file is downloaded and parsed *before* the database is
 * touched, and the replace is one transaction - the same two-part guarantee
 * sde.py and `storage.replace_sde_data` make together. It matters more here
 * than on desktop, not less: a phone loses connectivity mid-download as a
 * matter of routine, and the user of a half-replaced cache is not a developer
 * reading a traceback. A refresh that fails at any point - a dead network on
 * file five, the app being killed - leaves the previous cache exactly as it
 * was, and worst case the user has spent data for nothing.
 *
 * The cost of that guarantee is holding all six files' parsed rows in memory
 * at once (~50k types, ~5k stations, ~8k systems; on the order of 10-20MB of
 * objects, and far less than the raw CSV they came from, since only the
 * handful of columns below survive parsing). That is affordable on any
 * API-26-era device, and it is the reason SdeCsv streams rather than
 * materialising each file's text. If a future port adds the Production tables
 * this trade needs revisiting - `industryActivityMaterials.csv` alone is
 * larger than everything here. */
class SdeRepository(
    private val db: AppDatabase,
    private val downloader: SdeDownloader = SdeDownloader(),
) : ProductionCandidateSource, InventionSdeSource {
    private val dao = db.sdeDao()

    /** Downloads the current Fuzzwork dump and replaces the cache. Safe to
     * re-run at any time (e.g. after a CCP patch) - see this class's
     * docstring for the failure guarantees.
     *
     * `onProgress(file, index, total)` is called as each file *starts*
     * downloading, so the UI can say which of the six is in flight; there is
     * no byte-level progress because these responses are gzipped and
     * OkHttp's transparent decompression leaves no reliable content length to
     * divide by. */
    suspend fun refresh(
        onProgress: (file: SdeFile, index: Int, total: Int) -> Unit = { _, _, _ -> },
    ): SdeRefreshResult {
        // Captured before the real fetches, exactly as sde.py does: it
        // describes the dump we are about to download, so a refresh that
        // races a Fuzzwork regeneration records the older tag and the next
        // staleness check reports "newer available" rather than silently
        // claiming to be current.
        val dumpEtag = downloader.dumpEtag()
        val total = SdeFile.entries.size

        onProgress(SdeFile.TYPES, 1, total)
        val types = ArrayList<SdeTypeEntity>(60_000)
        downloader.fetchCsv(SdeFile.TYPES.filename) { reader ->
            types.clear() // a retried attempt re-parses from scratch
            SdeCsv.readRows(reader) { row ->
                val typeId = row.intOrNull("typeID") ?: return@readRows
                types.add(
                    SdeTypeEntity(
                        typeId = typeId,
                        groupId = row.intOrNull("groupID"),
                        typeName = row.text("typeName"),
                        volume = row.doubleOrNull("volume"),
                        published = row.boolAsInt("published"),
                        marketGroupId = row.intOrNull("marketGroupID"),
                        // Not a column in CCP's own invTypes table - Fuzzwork's
                        // dump adds it, which is why sde.py can read it here
                        // rather than from dogma attribute 633 the way
                        // EsiClient has to. Read leniently so a dump that ever
                        // drops the column degrades to null meta levels
                        // instead of an empty type table.
                        metaLevel = row.intOrNull("metaLevel"),
                        portionSize = row.intOrNull("portionSize"),
                    )
                )
            }
        }

        onProgress(SdeFile.GROUPS, 2, total)
        val groups = ArrayList<SdeGroupEntity>(2_000)
        downloader.fetchCsv(SdeFile.GROUPS.filename) { reader ->
            groups.clear()
            SdeCsv.readRows(reader) { row ->
                val groupId = row.intOrNull("groupID") ?: return@readRows
                groups.add(SdeGroupEntity(groupId, row.intOrNull("categoryID"), row.text("groupName")))
            }
        }

        onProgress(SdeFile.CATEGORIES, 3, total)
        val categories = ArrayList<SdeCategoryEntity>(64)
        downloader.fetchCsv(SdeFile.CATEGORIES.filename) { reader ->
            categories.clear()
            SdeCsv.readRows(reader) { row ->
                val categoryId = row.intOrNull("categoryID") ?: return@readRows
                categories.add(SdeCategoryEntity(categoryId, row.text("categoryName")))
            }
        }

        onProgress(SdeFile.MARKET_GROUPS, 4, total)
        val marketGroups = ArrayList<SdeMarketGroupEntity>(3_000)
        downloader.fetchCsv(SdeFile.MARKET_GROUPS.filename) { reader ->
            marketGroups.clear()
            SdeCsv.readRows(reader) { row ->
                val marketGroupId = row.intOrNull("marketGroupID") ?: return@readRows
                marketGroups.add(
                    SdeMarketGroupEntity(
                        marketGroupId,
                        row.intOrNull("parentGroupID"),
                        row.text("marketGroupName"),
                    )
                )
            }
        }

        onProgress(SdeFile.SOLAR_SYSTEMS, 5, total)
        val solarSystems = ArrayList<SdeSolarSystemEntity>(9_000)
        downloader.fetchCsv(SdeFile.SOLAR_SYSTEMS.filename) { reader ->
            solarSystems.clear()
            SdeCsv.readRows(reader) { row ->
                val systemId = row.longOrNull("solarSystemID") ?: return@readRows
                // Rows with no security value are skipped, matching sde.py's
                // own filter - they are the abstract/unreachable entries in
                // the map tables, not real systems a station can sit in.
                val security = row.doubleOrNull("security") ?: return@readRows
                solarSystems.add(
                    SdeSolarSystemEntity(
                        solarSystemId = systemId,
                        solarSystemName = row.text("solarSystemName"),
                        security = security,
                        regionId = row.longOrNull("regionID"),
                    )
                )
            }
        }

        onProgress(SdeFile.STATIONS, 6, total)
        val stations = ArrayList<SdeStationEntity>(6_000)
        downloader.fetchCsv(SdeFile.STATIONS.filename) { reader ->
            stations.clear()
            SdeCsv.readRows(reader) { row ->
                val stationId = row.longOrNull("stationID") ?: return@readRows
                // Same filter as sde.py: a station with no solar system is
                // useless to every query this table exists to answer.
                val systemId = row.longOrNull("solarSystemID") ?: return@readRows
                stations.add(SdeStationEntity(stationId, systemId, row.text("stationName")))
            }
        }

        onProgress(SdeFile.TYPE_MATERIALS, 7, total)
        val typeMaterials = ArrayList<SdeTypeMaterialEntity>(200_000)
        downloader.fetchCsv(SdeFile.TYPE_MATERIALS.filename) { reader ->
            typeMaterials.clear()
            SdeCsv.readRows(reader) { row ->
                val typeId = row.intOrNull("typeID") ?: return@readRows
                val materialTypeId = row.intOrNull("materialTypeID") ?: return@readRows
                val quantity = row.doubleOrNull("quantity") ?: return@readRows
                typeMaterials.add(SdeTypeMaterialEntity(typeId, materialTypeId, quantity))
            }
        }

        onProgress(SdeFile.BLUEPRINT_PRODUCTS, 8, total)
        val blueprintProducts = ArrayList<SdeBlueprintProductEntity>(30_000)
        downloader.fetchCsv(SdeFile.BLUEPRINT_PRODUCTS.filename) { reader ->
            blueprintProducts.clear()
            SdeCsv.readRows(reader) { row ->
                val activityId = row.intOrNull("activityID") ?: return@readRows
                // Manufacturing (1) and Invention (8) both matter now - see
                // RELEVANT_ACTIVITY_IDS and SdeBlueprintProductEntity's own
                // docstring for why the two share this one table.
                if (activityId !in RELEVANT_ACTIVITY_IDS) return@readRows
                val blueprintTypeId = row.intOrNull("typeID") ?: return@readRows
                val productTypeId = row.intOrNull("productTypeID") ?: return@readRows
                val quantity = row.doubleOrNull("quantity") ?: return@readRows
                blueprintProducts.add(
                    SdeBlueprintProductEntity(blueprintTypeId, activityId, productTypeId, quantity)
                )
            }
        }

        onProgress(SdeFile.BLUEPRINT_MATERIALS, 9, total)
        val blueprintMaterials = ArrayList<SdeBlueprintMaterialEntity>(300_000)
        downloader.fetchCsv(SdeFile.BLUEPRINT_MATERIALS.filename) { reader ->
            blueprintMaterials.clear()
            SdeCsv.readRows(reader) { row ->
                val activityId = row.intOrNull("activityID") ?: return@readRows
                if (activityId !in RELEVANT_ACTIVITY_IDS) return@readRows
                val blueprintTypeId = row.intOrNull("typeID") ?: return@readRows
                val materialTypeId = row.intOrNull("materialTypeID") ?: return@readRows
                val quantity = row.doubleOrNull("quantity") ?: return@readRows
                blueprintMaterials.add(
                    SdeBlueprintMaterialEntity(blueprintTypeId, activityId, materialTypeId, quantity)
                )
            }
        }

        onProgress(SdeFile.INVENTION_PROBABILITY, 10, total)
        val inventionProbability = ArrayList<SdeInventionProbabilityEntity>(20_000)
        downloader.fetchCsv(SdeFile.INVENTION_PROBABILITY.filename) { reader ->
            inventionProbability.clear()
            SdeCsv.readRows(reader) { row ->
                // The whole file is Invention (8) rows in practice - sde.py
                // filters explicitly too rather than assuming that, so this
                // does the same.
                val activityId = row.intOrNull("activityID") ?: return@readRows
                if (activityId != INVENTION_ACTIVITY_ID) return@readRows
                val t1BlueprintTypeId = row.intOrNull("typeID") ?: return@readRows
                val productTypeId = row.intOrNull("productTypeID") ?: return@readRows
                val probability = row.doubleOrNull("probability") ?: return@readRows
                inventionProbability.add(
                    SdeInventionProbabilityEntity(t1BlueprintTypeId, productTypeId, probability)
                )
            }
        }

        val refreshedAt = Instant.now().toString()
        // withTransaction is room-ktx's coroutine-aware counterpart of an
        // @Transaction DAO method: it pins every suspending call in the block
        // to one transaction on one connection, which a plain @Transaction
        // method cannot do across separate suspend DAO calls. Any exception
        // in here rolls the whole thing back, so the cache is either entirely
        // the old dump or entirely the new one - never a mix of the two.
        db.withTransaction {
            dao.clearTypes()
            dao.clearGroups()
            dao.clearCategories()
            dao.clearMarketGroups()
            dao.clearSolarSystems()
            dao.clearStations()
            dao.clearTypeMaterials()
            dao.clearBlueprintProducts()
            dao.clearBlueprintMaterials()
            dao.clearInventionProbability()
            dao.insertTypes(types)
            dao.insertGroups(groups)
            dao.insertCategories(categories)
            dao.insertMarketGroups(marketGroups)
            dao.insertSolarSystems(solarSystems)
            dao.insertStations(stations)
            dao.insertTypeMaterials(typeMaterials)
            dao.insertBlueprintProducts(blueprintProducts)
            dao.insertBlueprintMaterials(blueprintMaterials)
            dao.insertInventionProbability(inventionProbability)
            dao.upsertRefreshState(SdeRefreshStateEntity(refreshedAt = refreshedAt, dumpEtag = dumpEtag))
        }

        return SdeRefreshResult(refreshedAt = refreshedAt, rowCounts = rowCounts())
    }

    /** One HEAD request compared against the ETag recorded at the last
     * successful refresh - tells the caller whether a refresh is worth doing
     * without doing the download to find out. Ports `check_for_newer_sde`,
     * including its deliberate conservatism: `newerSdeAvailable` is only ever
     * true on a genuine mismatch, so "never refreshed here" and "server
     * unreachable" both report false rather than nagging on a guess. */
    suspend fun checkForNewerSde(): SdeStaleness {
        val state = refreshState()
        val remoteEtag = downloader.dumpEtag()
        return SdeStaleness(
            localRefreshedAt = state?.refreshedAt,
            remoteCheckSucceeded = remoteEtag != null,
            newerSdeAvailable = remoteEtag != null && state?.dumpEtag != null && remoteEtag != state.dumpEtag,
        )
    }

    suspend fun refreshState(): SdeRefreshStateEntity? = withContext(Dispatchers.IO) { dao.refreshState() }

    /** `{table: row count}` for every table in this port - the cheapest way
     * to tell an empty cache from a populated one, and what the UI shows
     * after a refresh. Keyed by the desktop build's table names so the two
     * builds' output reads the same. */
    suspend fun rowCounts(): Map<String, Int> = withContext(Dispatchers.IO) {
        linkedMapOf(
            SdeFile.TYPES.table to dao.countTypes(),
            SdeFile.GROUPS.table to dao.countGroups(),
            SdeFile.CATEGORIES.table to dao.countCategories(),
            SdeFile.MARKET_GROUPS.table to dao.countMarketGroups(),
            SdeFile.SOLAR_SYSTEMS.table to dao.countSolarSystems(),
            SdeFile.STATIONS.table to dao.countStations(),
            SdeFile.TYPE_MATERIALS.table to dao.countTypeMaterials(),
            SdeFile.BLUEPRINT_PRODUCTS.table to dao.countBlueprintProducts(),
            SdeFile.BLUEPRINT_MATERIALS.table to dao.countBlueprintMaterials(),
            SdeFile.INVENTION_PROBABILITY.table to dao.countInventionProbability(),
        )
    }

    // ------------------------------------------------------------ lookups
    // The reason this cache exists. Each of these is a single indexed query
    // against local storage in place of a live ESI round-trip; all of them
    // return null/empty on a cache that has never been refreshed, which is
    // the same "degrade to the slow path" behaviour the desktop build has
    // before its first `refresh-sde`. Candidate Discovery, Realized Trades,
    // the Doctrine EFT parser, and Reprocessing Quote all read some of
    // these now; Station Trading's own candidate discovery is the one
    // tracked follow-up left (see ROADMAP.md's Android section).

    /** The SDE name for a type id - what `/universe/types/{id}/` costs a
     * network round-trip to answer. Also the `InventionSdeSource.typeName`
     * implementation - the Invention Estimator's own use of this same
     * lookup (naming a T1 blueprint/relic and its invented product) needs
     * nothing this general-purpose one doesn't already do. */
    override suspend fun typeName(typeId: Int): String? = withContext(Dispatchers.IO) { dao.typeName(typeId) }

    /** The real SDE category name for a type (type -> group -> category), as
     * opposed to CandidateDiscovery's `guessCategory` string heuristic, which
     * only exists because this lookup had no cache to run against. */
    suspend fun categoryNameFor(typeId: Int): String? =
        withContext(Dispatchers.IO) { dao.categoryNameFor(typeId) }

    /** Every NPC station id in a region. This is what location filtering
     * (e.g. Realized Trades deciding whether a transaction happened "in the
     * reference region") needs and currently has no cheap way to get -
     * without it the only answer is walking ESI region by region. */
    suspend fun stationIdsInRegion(regionId: Long): List<Long> =
        withContext(Dispatchers.IO) { dao.stationIdsInRegion(regionId) }

    /** Same question, one solar system wide - mirrors
     * `get_station_ids_in_system`. */
    suspend fun stationIdsInSystem(systemId: Long): List<Long> =
        withContext(Dispatchers.IO) { dao.stationIdsInSystem(systemId) }

    suspend fun type(typeId: Int): SdeTypeEntity? = withContext(Dispatchers.IO) { dao.type(typeId) }

    /** Exact, case-insensitive name -> type/group/category lookup - see
     * `SdeDao.resolveTypeByName`'s own doc. Used by the Doctrine EFT parser
     * to resolve a pasted fitting line's item name. */
    suspend fun resolveTypeByName(name: String): SdeTypeNameResolution? =
        withContext(Dispatchers.IO) { dao.resolveTypeByName(name) }

    /** Every Ship/Structure type name in the cache - see
     * `SdeDao.hullTypeNames`'s own doc. */
    suspend fun hullTypeNames(): List<String> = withContext(Dispatchers.IO) { dao.hullTypeNames() }

    /** Exact, case-insensitive name -> type id - what a Reprocessing Quote
     * paste line's own item name resolves through (see
     * `data/refining/ReprocessingQuote.kt`). Null on no match, same
     * "unknown, don't guess" contract as `typeName`'s reverse lookup. */
    suspend fun resolveTypeIdByName(name: String): Int? =
        withContext(Dispatchers.IO) { dao.resolveTypeIdByName(name) }

    /** `SdeTypeEntity.portionSize` for one type - see that field's own
     * docstring. */
    suspend fun portionSize(typeId: Int): Int? = withContext(Dispatchers.IO) { dao.portionSize(typeId) }

    /** `{material_type_id: quantity per whole portion}` for one type - the
     * counterpart of storage.py's `get_type_materials`. Empty (not null) for
     * a type with no material rows at all. */
    suspend fun typeMaterials(typeId: Int): List<Pair<Int, Double>> = withContext(Dispatchers.IO) {
        dao.typeMaterials(typeId).map { it.materialTypeId to it.quantity }
    }

    /** Every published compressed ore/ice type in the cache - the Ore
     * Shortlist's fixed candidate universe, see `SdeDao.oreIceCandidateTypes`
     * and `data/refining/OreShortlist.kt`'s `buildOreCandidateUniverse`. */
    suspend fun oreIceCandidateTypes(): List<OreIceCandidateTypeRow> =
        withContext(Dispatchers.IO) { dao.oreIceCandidateTypes() }

    // -------------------------------------------------------- bulk reads
    // Whole-table reads for CandidateDiscovery.buildCandidateUniverseFromSde
    // - see SdeDao's own comment on why these differ from the single-row
    // lookups above.

    suspend fun marketGroups(): List<SdeMarketGroupEntity> = withContext(Dispatchers.IO) { dao.allMarketGroups() }

    suspend fun typesWithMarketGroup(): List<SdeTypeEntity> = withContext(Dispatchers.IO) { dao.typesWithMarketGroup() }

    /** `{category_id: category_name}` - the counterpart of storage.py's
     * `load_sde_category_names`. */
    suspend fun categoryNames(): Map<Int, String> = withContext(Dispatchers.IO) {
        dao.allCategories().associate { it.categoryId to (it.categoryName ?: "") }
    }

    /** `{group_id: category_id}` - joined with `SdeTypeEntity.groupId`
     * (already present on every type row) by the caller to get a type's
     * category_id without a second per-type_id query, the same
     * whole-table-then-join-in-memory shape `buildCandidateUniverseFromSde`
     * uses throughout. */
    suspend fun groupCategoryIds(): Map<Int, Int> = withContext(Dispatchers.IO) {
        dao.allGroups().mapNotNull { g -> g.categoryId?.let { g.groupId to it } }.toMap()
    }

    // ------------------------------------------------ blueprint BOM lookups
    // Backs data/production/ProductionBuildCost.kt's recursive build-cost
    // walk. Both mirror storage.py's get_blueprint_for_product/
    // get_blueprint_materials shape, narrowed to the one activity
    // (Manufacturing) this cache stores - see SdeBlueprintProductEntity's
    // own docstring for why no activity_id parameter is needed here the way
    // the desktop functions take/return one.

    /** "Which blueprint makes `productTypeId`, and how many per run" - null
     * for a raw material or anything this cache has no Manufacturing
     * blueprint for. */
    override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? =
        withContext(Dispatchers.IO) {
            dao.blueprintForProduct(productTypeId)?.let { BlueprintForProduct(it.blueprintTypeId, it.quantity) }
        }

    /** One run's Manufacturing materials for `blueprintTypeId`, at ME 0 -
     * applying ME reduction is the caller's job, matching
     * `storage.get_blueprint_materials`'s own contract. Empty (not null) for
     * a blueprint id with no material rows cached. */
    override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> =
        withContext(Dispatchers.IO) {
            dao.blueprintMaterials(blueprintTypeId).map { it.materialTypeId to it.quantity }
        }

    override suspend fun volumeOf(typeId: Int): Double? = withContext(Dispatchers.IO) { dao.type(typeId)?.volume }

    /** Build Candidates' whole-catalog scan universe - every published type
     * with a cached Manufacturing blueprint (`SdeDao.manufacturableTypes`,
     * see that query's own docstring). */
    override suspend fun manufacturableTypes(): List<CandidateType> = withContext(Dispatchers.IO) {
        dao.manufacturableTypes().mapNotNull { t ->
            val name = t.typeName ?: return@mapNotNull null
            CandidateType(typeId = t.typeId, typeName = name, volumeM3 = t.volume, metaLevel = t.metaLevel)
        }
    }

    // -------------------------------------------------- invention lookups
    // Implements InventionSdeSource for data/production/InventionEstimator.kt
    // - see SdeDao's own "invention" section for the underlying queries.

    /** The full Invention (activity 8) job definition for
     * `t1BlueprintTypeId` - null if it invents nothing at all. Two separate
     * DAO calls (product row, then materials) rather than one join: the
     * product row's absence is the "invents nothing" signal, so it is
     * checked first and short-circuits before the materials query runs. */
    override suspend fun inventionRecipe(t1BlueprintTypeId: Int): InventionRecipe? =
        withContext(Dispatchers.IO) {
            val product = dao.inventionProduct(t1BlueprintTypeId) ?: return@withContext null
            val baseProbability = dao.inventionProbability(t1BlueprintTypeId, product.productTypeId)
            val datacores = dao.inventionMaterials(t1BlueprintTypeId).map { it.materialTypeId to it.quantity }
            InventionRecipe(
                productTypeId = product.productTypeId,
                baseRuns = product.quantity.toInt(),
                baseProbability = baseProbability,
                datacores = datacores,
            )
        }

    override suspend fun inventionRecipeCandidates(productBlueprintTypeId: Int): List<Int> =
        withContext(Dispatchers.IO) { dao.inventionRecipeCandidates(productBlueprintTypeId) }

    override suspend fun resolveInventionProductTypeId(productName: String): Int? =
        withContext(Dispatchers.IO) { dao.resolveInventionProductTypeId(productName) }

    override suspend fun categoryIdOf(typeId: Int): Int? =
        withContext(Dispatchers.IO) { dao.categoryIdFor(typeId) }
}
