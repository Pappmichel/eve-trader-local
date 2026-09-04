package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.IndustryJob
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import java.time.Instant

/** Read-only views over a producer character's *live* ESI industry-job
 * list - a Kotlin port of the desktop build's `production/jobs.py`
 * (`list_current_jobs`/`character_slot_overview`), needing no new SDE
 * schema at all: `EsiClient.characterIndustryJobs` is this port's only new
 * data source, everything else reuses [ProductionBomSource]/[OrderStats]
 * plumbing that already exists for [ProductionBuildCost].
 *
 * One documented *improvement* over the desktop build's own reduced
 * `character_slot_overview`, not a further simplification: that function's
 * own docstring explains its total/free-slot fields are dropped because
 * `production/esi_sync.py` "never requests the character-skills ESI scope"
 * needed to derive them. This Android app's `EsiClient` already has
 * `characterSkills` (added for Station Trading's own order-slot count,
 * `esi-skills.read_skills.v1`) and this port's own "Producer" login role
 * requests that exact scope for the industry-jobs pull anyway - so
 * [characterSlotOverview] below restores the real total/free-slot columns
 * the desktop build's *parent* app has, rather than reproducing this
 * repo's own narrower reduction.
 *
 * Simplifications vs. desktop `jobs.py`, documented rather than silent:
 * - **Live, not synced.** Desktop reads a local cache `production/esi_sync
 *   .py` populates ahead of time (corp jobs included, from every
 *   logged-in producer character, merged and deduplicated once). This port
 *   fetches directly from ESI per screen visit, for whichever producer
 *   characters are currently logged in - no local persistence, no
 *   corp-role-driven multi-character corp-job merge beyond "ask each
 *   logged-in producer character for their own view and de-duplicate by
 *   job_id" (a corp job is visible to more than one logged-in installer
 *   only in the unlikely case two logged-in characters are in the same
 *   corp with industry roles - see `ProductionJobsScreen.kt`).
 * - **`quantity` is looked up by product_type_id only, not (blueprint_type_
 *   id, activity_id, product_type_id).** `ProductionBomSource.
 *   blueprintForProduct` (Manufacturing-only, see `ProductionBuildCost.kt`)
 *   has no activity_id axis to match desktop's `storage.get_product_
 *   quantity` against - in practice this only matters for a job whose
 *   product happens to have *both* a Manufacturing and some other-activity
 *   formula, which real EVE data never actually does (see
 *   `SdeBlueprintProductEntity`'s own docstring on why this cache is
 *   Manufacturing-only at all). A Reaction/Invention/Copying job's
 *   product_type_id correctly resolves to no quantity, the same "no cached
 *   BOM data for this activity" outcome desktop's own function documents
 *   for research jobs (which have no product_type_id in the first place).
 */

// ------------------------------------------------------------- job slots
// Mirrors production/constants.py's own job-slots section exactly - see
// that module's own comment for the real EVE mechanic these encode (job
// slots are governed by skills, independently per job category, since the
// Ascension expansion split them).
const val SKILL_MASS_PRODUCTION = 3387
const val SKILL_ADVANCED_MASS_PRODUCTION = 24625
const val SKILL_MASS_REACTIONS = 45748
const val SKILL_ADVANCED_MASS_REACTIONS = 45749
const val SKILL_LABORATORY_OPERATION = 3406
const val SKILL_ADVANCED_LABORATORY_OPERATION = 24624

/** ESI `activity_id` -> which slot category it draws from - matches
 * `constants.ACTIVITY_SLOT_CATEGORY` exactly, including the same "no
 * activity_id 7 (Reverse Engineering)" omission (that Ancient-Relic-based
 * Tech III mechanic was removed from EVE years ago; no live ESI industry
 * job can ever report it). */
val ACTIVITY_SLOT_CATEGORY: Map<Int, String> = mapOf(
    1 to "manufacturing", // Manufacturing
    11 to "reaction",     // Reaction
    3 to "science",       // Time Efficiency Research
    4 to "science",       // Material Efficiency Research
    5 to "science",       // Copying
    8 to "science",       // Invention
)

/** ESI `activity_id` -> display label - matches `constants.ACTIVITY_JOB_LABELS`. */
val ACTIVITY_JOB_LABELS: Map<Int, String> = mapOf(
    1 to "Manufacturing",
    11 to "Reaction",
    3 to "TE Research",
    4 to "ME Research",
    5 to "Copying",
    8 to "Invention",
)

/** Slot category key -> display label - matches `constants.SLOT_CATEGORY_LABELS`. */
val SLOT_CATEGORY_LABELS: Map<String, String> = mapOf(
    "manufacturing" to "Manufacturing",
    "reaction" to "Reactions",
    "science" to "Science",
)

/** A job counts toward slot usage only in one of these ESI-reported
 * statuses - matches `jobs.character_slot_overview`'s own filter exactly
 * ("delivered"/"cancelled"/"reverted" jobs no longer occupy a slot). */
private val ACTIVE_JOB_STATUSES = setOf("active", "paused", "ready")

/** `{skill_type_id: active_skill_level}` -> `{"manufacturing"|"reaction"|
 * "science": total_slot_count}` - a direct port of `constants.
 * job_slots_from_skills`: base 1 slot + 1 per level of the base skill + 1
 * per level of its "Advanced" counterpart (max 5 each, so up to 11 slots
 * per category), independently for each of the three job categories. */
fun jobSlotsFromSkills(skillLevels: Map<Int, Int>): Map<String, Int> {
    fun slots(baseId: Int, advancedId: Int): Int =
        1 + (skillLevels[baseId] ?: 0) + (skillLevels[advancedId] ?: 0)

    return mapOf(
        "manufacturing" to slots(SKILL_MASS_PRODUCTION, SKILL_ADVANCED_MASS_PRODUCTION),
        "reaction" to slots(SKILL_MASS_REACTIONS, SKILL_ADVANCED_MASS_REACTIONS),
        "science" to slots(SKILL_LABORATORY_OPERATION, SKILL_ADVANCED_LABORATORY_OPERATION),
    )
}

// --------------------------------------------------------------- rows

/** One active industry job (character or corp), shown individually even if
 * several jobs build the same item - mirrors `models.IndustryJobRow`
 * exactly, minus the desktop dataclass's own field ordering (irrelevant in
 * Kotlin). */
data class IndustryJobRow(
    val jobId: Long,
    val typeName: String,
    val activity: String,
    val runs: Int,
    val quantity: Double?,
    val outputValue: Double?,
    val status: String,
    val startDate: String,
    val endDate: String,
    val remainingSeconds: Double?,
    val installerName: String,
)

/** Per-character, per-slot-category count of currently active/paused/ready
 * industry jobs installed by that character, alongside the real total/free
 * slot count derived from their trained skills - see this file's module
 * docstring for why this Android port can show totals where the local
 * desktop build's own reduced `CharacterSlotRow` cannot. `totalSlots`/
 * `freeSlots` are null when no skill data was supplied for that character
 * (e.g. `characterSkills` failed or the character hasn't logged in with the
 * `esi-skills.read_skills.v1` scope) - "usage without a known total" is
 * still genuinely useful on its own, same as the desktop build's own
 * always-total-less version. */
data class CharacterSlotRow(
    val characterName: String,
    val jobType: String,
    val usedSlots: Int,
    val totalSlots: Int?,
    val freeSlots: Int?,
)

/** Every active/paused/ready/delivered job across `jobs`, one row per job,
 * sorted by soonest-completing first - mirrors `jobs.list_current_jobs`
 * exactly. `home`/`jita` need only cover the distinct product_type_ids
 * these jobs actually output (mirrors that function's own scoping
 * comment); `installerNames` maps an ESI `installer_id` to a display name
 * (the logged-in producer character it belongs to, or any name the caller
 * can resolve) - an unresolvable id falls back to the bare id itself,
 * never silently dropped. */
suspend fun buildIndustryJobRows(
    jobs: List<IndustryJob>,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    installerNames: Map<Long, String>,
    typeNames: Map<Int, String>,
    now: Instant = Instant.now(),
): List<IndustryJobRow> {
    val rows = jobs.map { job ->
        val qtyPerRun = job.productTypeId?.let { bom.blueprintForProduct(it)?.productQuantity }
        val quantity = qtyPerRun?.times(job.runs)

        var outputValue: Double? = null
        if (quantity != null) {
            // quantity is only ever non-null when job.productTypeId itself is
            // (see qtyPerRun above), so this is a safe non-null read.
            val homeSell = home[job.productTypeId]?.sellPercentile
            val jitaSell = jita[job.productTypeId]?.sellPercentile
            outputValue = when {
                homeSell != null && homeSell > 0 -> quantity * homeSell
                jitaSell != null && jitaSell > 0 -> quantity * jitaSell
                else -> null
            }
        }

        val remaining = job.endDate.takeIf { it.isNotEmpty() }?.let { end ->
            (Instant.parse(end).epochSecond - now.epochSecond).toDouble()
        }

        // The produced item's name where there is one, else the blueprint's
        // own name (a research/copying job has no product_type_id at all) -
        // matches jobs.list_current_jobs' own `type_name or str(product_
        // type_id) or "?"` fallback chain, just resolved against a caller-
        // supplied name map (typeNames) instead of a stored column, since
        // this port fetches jobs live rather than through a synced table
        // that already carries a resolved name per row.
        val displayTypeId = job.productTypeId ?: job.blueprintTypeId
        val typeName = typeNames[displayTypeId] ?: (job.productTypeId?.toString() ?: "?")

        IndustryJobRow(
            jobId = job.jobId,
            typeName = typeName,
            activity = ACTIVITY_JOB_LABELS[job.activityId] ?: job.activityId.toString(),
            runs = job.runs,
            quantity = quantity,
            outputValue = outputValue,
            status = job.status,
            startDate = job.startDate,
            endDate = job.endDate,
            remainingSeconds = remaining,
            installerName = installerNames[job.installerId] ?: job.installerId.toString(),
        )
    }
    return rows.sortedBy { it.remainingSeconds ?: Double.POSITIVE_INFINITY }
}

/** Per-character, per-slot-category *usage* + real total/free slots (see
 * this file's module docstring) - a direct port of `jobs.
 * character_slot_overview`'s usage-counting loop, extended with
 * [jobSlotsFromSkills] wherever `skillLevelsByCharacter` has an entry for
 * that installer. Only categories with at least one currently-used slot are
 * shown, matching desktop's own `if used_count == 0: continue` exactly -
 * this is a job-activity summary, not a full "here is every skill you
 * could train" browser. */
fun characterSlotOverview(
    jobs: List<IndustryJob>,
    installerNames: Map<Long, String>,
    skillLevelsByCharacter: Map<Long, Map<Int, Int>> = emptyMap(),
): List<CharacterSlotRow> {
    val used = mutableMapOf<Pair<Long, String>, Int>()
    val installerIds = mutableSetOf<Long>()
    for (job in jobs) {
        if (job.status !in ACTIVE_JOB_STATUSES) continue
        val category = ACTIVITY_SLOT_CATEGORY[job.activityId] ?: continue
        if (installerNames[job.installerId] == null) continue
        installerIds += job.installerId
        val key = job.installerId to category
        used[key] = (used[key] ?: 0) + 1
    }

    val rows = mutableListOf<CharacterSlotRow>()
    for (installerId in installerIds.sortedBy { installerNames[it] }) {
        val name = installerNames.getValue(installerId)
        val totals = skillLevelsByCharacter[installerId]?.let { jobSlotsFromSkills(it) }
        for (category in listOf("manufacturing", "reaction", "science")) {
            val usedCount = used[installerId to category] ?: 0
            if (usedCount == 0) continue
            val total = totals?.get(category)
            rows += CharacterSlotRow(
                characterName = name,
                jobType = SLOT_CATEGORY_LABELS.getValue(category),
                usedSlots = usedCount,
                totalSlots = total,
                freeSlots = total?.let { it - usedCount },
            )
        }
    }
    return rows
}
