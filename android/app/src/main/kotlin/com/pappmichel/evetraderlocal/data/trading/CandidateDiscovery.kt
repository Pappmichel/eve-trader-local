package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.metaLevel
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext

/** Market-group-based candidate discovery - a Kotlin port of the desktop
 * build's candidate_discovery.py, both halves: `buildCandidateUniverseFromSde`
 * (the SDE-backed fast path, `_build_candidate_universe_from_sde`) and
 * `buildCandidateUniverse`'s live-ESI-walk fallback
 * (`_build_candidate_universe_from_esi`), used only when the SDE cache is
 * empty - the same "prefer the local cache, fall back to ~2000 live ESI
 * calls" branch desktop's own `build_candidate_universe` takes.
 *
 * One documented simplification in the SDE-backed path vs. desktop: capital
 * modules (category_id=MODULE_CATEGORY_ID) have a much smaller *packaged*
 * volume than the raw SDE `volume` column, which desktop corrects with a
 * bulk ESI lookup (`resolve_effective_volume_bulk`) before returning. That
 * correction is not ported here - it would mean an ESI round-trip for every
 * capital module even on the fast, cache-only path, defeating the point of
 * having a cache at all - so a capital module's volume here is the raw SDE
 * figure, over-stating its true packaged volume. Ships have the identical
 * quirk but are already excluded by `isWantedMarketPath`
 * (`excludedPathPrefixes`), so only Module-category types are affected.
 *
 * Concurrency: the desktop version's ESI-walk path is a plain sequential
 * loop; `resolve_effective_volume_bulk` elsewhere in that same file uses a
 * `max_workers=10` thread pool for its own bulk ESI lookups, and this port
 * follows that same precedent (a `Semaphore(10)`-bounded fan-out) rather
 * than a literal one-at-a-time port, since a phone waiting through ~2000
 * sequential round-trips is a materially worse experience than the same
 * wait on a desktop CLI/GUI run. */
object CandidateDiscovery {
    // SDE category_id=7 ("Module") covers both regular modules and rigs.
    const val MODULE_CATEGORY_ID = 7

    // SDE category_id=20 ("Implant") covers both real cyberimplants and
    // Boosters/Drugs - CCP doesn't split them into separate categories, so
    // category alone labels both as "Implant". group_id=303 is what
    // actually distinguishes them (confirmed against live SDE data on
    // desktop - an earlier guess of 746 turned out to be a skill-hardwiring
    // implant group, not boosters at all).
    const val IMPLANT_CATEGORY_ID = 20
    const val BOOSTER_GROUP_ID = 303

    private const val TYPE_INFO_CONCURRENCY = 10

    fun isWantedMarketPath(path: String, config: TradingConfig): Boolean {
        val lower = path.lowercase()
        return config.excludedPathPrefixes.none { lower.startsWith(it) }
    }

    /** Display category for the shortlist's category column - never used
     * for any margin/filtering math, matching candidate_discovery.py's own
     * `guess_category` docstring. Prefers the *real* SDE category name
     * (e.g. "Implant", "Charge", "Drone", "Skill", "Material") via
     * `categoryNames` (`SdeRepository`'s categories table, from Fuzzwork's
     * invCategories.csv) whenever `categoryId` is available - only the
     * live-ESI-walk path (`buildCandidateUniverse`'s fallback branch, see
     * this file's own docstring) has no per-type category to give it,
     * since fetching one would mean an extra ESI call per type; that path
     * falls back to the old "module"/"rig"-in-name-or-path, or a volume
     * >=5m3, heuristic below.
     *
     * `groupId` (only ever available on the SDE-backed path) splits
     * Boosters/Drugs out of the "Implant" category they would otherwise
     * share with real cyberimplants - see `IMPLANT_CATEGORY_ID`'s own
     * comment. */
    fun guessCategory(
        path: String,
        itemName: String,
        volumeM3: Double,
        categoryId: Int? = null,
        categoryNames: Map<Int, String>? = null,
        groupId: Int? = null,
    ): String {
        if (categoryId != null) {
            if (categoryId == IMPLANT_CATEGORY_ID && groupId == BOOSTER_GROUP_ID) return "Drugs"
            categoryNames?.get(categoryId)?.let { return it }
            return if (categoryId == MODULE_CATEGORY_ID) "Module/Rig" else "Material"
        }
        val s = "$path $itemName".lowercase()
        return if ("module" in s || "rig" in s || volumeM3 >= 5.0) "Module/Rig" else "Material"
    }

    // internal (not private) so ShortlistTest-style JUnit tests in this
    // module can exercise it directly, the same way candidate_discovery.py's
    // own _market_group_path is imported straight into
    // tests/test_candidate_discovery.py despite its underscore prefix.
    internal fun marketGroupPath(groupId: Int, names: Map<Int, String>, parents: Map<Int, Int>): String {
        val parts = mutableListOf<String>()
        var cur = groupId
        var guard = 0
        while (cur > 0 && guard < 20) {
            parts.add(0, names[cur] ?: "?")
            cur = parents[cur] ?: 0
            guard++
        }
        return parts.joinToString(" > ")
    }

    /** Builds the candidate universe from the local SDE cache
     * (`SdeRepository`) - no ESI calls at all - or returns null if that
     * cache hasn't been populated yet (`SdeDataScreen`'s Refresh never run),
     * mirroring desktop's own "market_groups and sde_types both non-empty"
     * gate. `buildCandidateUniverse` below is what actually falls back to
     * the live-ESI walk on a null result. */
    suspend fun buildCandidateUniverseFromSde(sde: SdeRepository, config: TradingConfig): List<Candidate>? {
        val marketGroups = sde.marketGroups()
        val types = sde.typesWithMarketGroup()
        if (marketGroups.isEmpty() || types.isEmpty()) return null

        val names = marketGroups.associate { it.marketGroupId to (it.marketGroupName ?: "") }
        val parents = marketGroups.associate { it.marketGroupId to (it.parentGroupId ?: 0) }
        val wantedPaths = names.keys.mapNotNull { gid ->
            val path = marketGroupPath(gid, names, parents)
            if (isWantedMarketPath(path, config)) gid to path else null
        }.toMap()

        val categoryNames = sde.categoryNames()
        val groupCategoryIds = sde.groupCategoryIds()

        val candidates = mutableListOf<Candidate>()
        for (type in types) {
            val marketGroupId = type.marketGroupId ?: continue
            val path = wantedPaths[marketGroupId] ?: continue
            val typeName = type.typeName
            val volume = type.volume
            if (typeName.isNullOrEmpty() || volume == null || volume <= 0.0) continue
            val categoryId = type.groupId?.let { groupCategoryIds[it] }
            candidates.add(
                Candidate(
                    item = typeName,
                    typeId = type.typeId,
                    volumeM3 = volume,
                    category = guessCategory(path, typeName, volume, categoryId, categoryNames, type.groupId),
                    marketGroupPath = path,
                    metaLevel = type.metaLevel,
                )
            )
        }
        return candidates
    }

    /** Prefers the local SDE cache (`buildCandidateUniverseFromSde`, no ESI
     * calls at all) when it's populated, falling back to the slower live-ESI
     * walk below only on a fresh/never-refreshed cache - the same branch
     * desktop's own `build_candidate_universe` takes. `onProgress` is only
     * ever called on the live-ESI fallback path; the SDE path has no
     * per-item network round-trip to report progress through. */
    suspend fun buildCandidateUniverse(
        config: TradingConfig,
        sde: SdeRepository? = null,
        client: EsiClient = EsiClient(),
        onProgress: (done: Int, total: Int) -> Unit = { _, _ -> },
    ): List<Candidate> {
        if (sde != null) {
            buildCandidateUniverseFromSde(sde, config)?.let { return it }
        }
        return buildCandidateUniverseFromEsi(config, client, onProgress)
    }

    /** `onProgress(done, total)` is called after each type is resolved, so a
     * caller can show real progress through what is - without a local SDE
     * cache - a genuinely slow, ~2000-call operation. */
    suspend fun buildCandidateUniverseFromEsi(
        config: TradingConfig,
        client: EsiClient = EsiClient(),
        onProgress: (done: Int, total: Int) -> Unit = { _, _ -> },
    ): List<Candidate> = coroutineScope {
        val groupIds = client.listMarketGroupIds()

        val names = mutableMapOf<Int, String>()
        val parents = mutableMapOf<Int, Int>()
        val groupTypes = mutableMapOf<Int, List<Int>>()
        val semaphore = Semaphore(TYPE_INFO_CONCURRENCY)

        val groupInfos = groupIds.map { groupId ->
            async(Dispatchers.IO) { semaphore.withPermit { groupId to client.getMarketGroup(groupId) } }
        }.map { it.await() }
        for ((groupId, info) in groupInfos) {
            names[groupId] = info.name
            parents[groupId] = info.parentGroupId ?: 0
            groupTypes[groupId] = info.types
        }

        val typePaths = mutableMapOf<Int, String>()
        for (groupId in names.keys) {
            val path = marketGroupPath(groupId, names, parents)
            if (isWantedMarketPath(path, config)) {
                for (typeId in groupTypes[groupId].orEmpty()) {
                    typePaths.putIfAbsent(typeId, path)
                }
            }
        }

        val done = AtomicInteger(0)
        val total = typePaths.size
        val candidateDeferreds = typePaths.map { (typeId, path) ->
            async(Dispatchers.IO) {
                semaphore.withPermit {
                    val info = client.getTypeInfo(typeId)
                    val volume = info.packagedVolume ?: info.volume ?: 0.0
                    val candidate = if (info.name.isNotEmpty() && volume > 0.0) {
                        Candidate(
                            item = info.name,
                            typeId = typeId,
                            volumeM3 = volume,
                            category = guessCategory(path, info.name, volume),
                            marketGroupPath = path,
                            metaLevel = info.metaLevel(),
                        )
                    } else {
                        null
                    }
                    onProgress(done.incrementAndGet(), total)
                    candidate
                }
            }
        }
        withContext(Dispatchers.Default) { candidateDeferreds.map { it.await() }.filterNotNull() }
    }
}
