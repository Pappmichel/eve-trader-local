package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.metaLevel
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext

/** Market-group-based candidate discovery - the live-ESI-walk half of the
 * desktop build's candidate_discovery.py (`_build_candidate_universe_from_esi`)
 * only. The desktop version prefers a local Fuzzwork SDE cache
 * (`_build_candidate_universe_from_sde`) when populated, since walking
 * ESI's market-group tree live is ~2000+ separate calls; porting that SDE
 * cache (sde.py's Fuzzwork CSV download/parse pipeline, several SQLite
 * tables) is real, separate work not done yet (see ROADMAP.md's Android
 * section) - this always takes the slower live-ESI path, same as a fresh
 * desktop install that hasn't run `refresh-sde` yet. `guess_category`
 * likewise only has the string-heuristic fallback here, for the same
 * reason (no local SDE category names to look up).
 *
 * Concurrency: the desktop version's ESI-walk path is a plain sequential
 * loop; `resolve_effective_volume_bulk` elsewhere in that same file uses a
 * `max_workers=10` thread pool for its own bulk ESI lookups, and this port
 * follows that same precedent (a `Semaphore(10)`-bounded fan-out) rather
 * than a literal one-at-a-time port, since a phone waiting through ~2000
 * sequential round-trips is a materially worse experience than the same
 * wait on a desktop CLI/GUI run. */
object CandidateDiscovery {
    // SDE category_id=7 ("Module") - not resolvable without the SDE cache,
    // kept only as a comment for parity with candidate_discovery.py's own
    // MODULE_CATEGORY_ID; the string-heuristic guessCategory below doesn't
    // need the numeric id.
    private const val TYPE_INFO_CONCURRENCY = 10

    fun isWantedMarketPath(path: String, config: TradingConfig): Boolean {
        val lower = path.lowercase()
        return config.excludedPathPrefixes.none { lower.startsWith(it) }
    }

    /** String-heuristic fallback only - see this file's own docstring for
     * why the SDE-backed real-category-name path (candidate_discovery.py's
     * `guess_category` when `category_names` is available) isn't ported
     * yet. */
    fun guessCategory(path: String, itemName: String, volumeM3: Double): String {
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

    /** `onProgress(done, total)` is called after each type is resolved, so a
     * caller can show real progress through what is - without a local SDE
     * cache - a genuinely slow, ~2000-call operation. */
    suspend fun buildCandidateUniverse(
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
