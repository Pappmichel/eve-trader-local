package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.time.Instant
import java.util.UUID

/** One persisted line from a saved fitting - the Android counterpart of the
 * desktop build's `FittingItem` row (`doctrine/models.py`), minus the
 * `fitting_id` foreign key (it lives one level up, as a field on
 * [SavedFitting] itself, not duplicated onto every one of its own items -
 * see [SavedFitting]'s own docstring on why this whole thing is one JSON
 * blob rather than a normalized table). */
@Serializable
data class SavedFittingItem(
    val lineNo: Int,
    val slotSection: String,
    val typeId: Int,
    val quantity: Double,
    val isOffline: Boolean = false,
)

/** A named, persisted fitting - what `doctrine/actions.py`'s `do_add_fitting`
 * writes into desktop's `doctrine_fitting`/`doctrine_fitting_item` SQLite
 * tables, flattened here into one JSON document (same one-blob-per-scope
 * shape [ShortlistRepository]/[StationTradingShortlistRepository] already
 * use for this platform's Room `settings` table) rather than two normalized
 * tables + a Room migration. This is the same "reuse the existing settings-
 * blob pattern instead of standing up new Room entities/DAOs/migrations for
 * one new list" tradeoff every other list on this platform already makes -
 * a real multi-fitting/multi-doctrine deployment would eventually want real
 * foreign keys (esp. once contract sync needs to join against a `doctrine_id`
 * the way desktop's schema does), but nothing on this platform groups
 * fittings into doctrines yet (no Doctrine-list screen exists here), so
 * `doctrineId` is carried as a plain free-text label for now, not a foreign
 * key into a `doctrines` table that doesn't exist.
 *
 * `items` are the already-parsed [ParsedItem]s from [EftFittingParser] at
 * save time - re-parsing on every load would mean a saved fit silently
 * changes shape if the local SDE cache is later refreshed and a name now
 * resolves differently; storing the resolved snapshot instead matches
 * desktop's own `doctrine_fitting_item` table, which is likewise a persisted
 * parse result, not a live re-parse of `raw_eft` on every read (`raw_eft` is
 * kept too, purely for re-display/re-editing, same as desktop's own
 * `Fitting.raw_eft` field). */
@Serializable
data class SavedFitting(
    val fittingId: String,
    val name: String,
    val doctrineId: String = "",
    val hullTypeId: Int,
    val hullName: String,
    val rawEft: String,
    val items: List<SavedFittingItem>,
    val contractTarget: Int = 0,
    val stockpileTarget: Int = 0,
    val cargoTolerancePct: Double? = null,
    val active: Boolean = true,
    val createdAt: String,
)

/** Persists saved Doctrine fittings - the Android counterpart of desktop's
 * `storage.create_fitting`/`list_fittings_for_doctrine`/`delete_fitting`
 * trio (`doctrine/actions.py`'s `do_add_fitting`/`do_list_fittings`/
 * `do_delete_fitting`), as one JSON blob under its own scope in the shared
 * `settings` table - same shape as [ShortlistRepository]/
 * [StationTradingShortlistRepository]. This is what unblocks Stockpile
 * Status/Shopping List: both need "every saved fitting and how much of each
 * item it calls for", which requires *some* persistence layer to exist
 * first - this is that layer. */
class DoctrineFittingRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }
    private val serializer = ListSerializer(SavedFitting.serializer())

    // Guards save-after-load sequences (add/update/delete) the same way
    // ShortlistRepository.addIfAbsent's own mutex does - two screens (or two
    // quick taps) racing a load-modify-save round trip must not silently
    // drop one side's write.
    private val writeMutex = Mutex()

    companion object {
        const val SCOPE = "doctrine_fittings"
    }

    suspend fun list(): List<SavedFitting> = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(SCOPE) ?: return@withContext emptyList()
        try {
            json.decodeFromString(serializer, stored.overridesJson)
        } catch (e: Exception) {
            emptyList()
        }
    }

    suspend fun listActive(): List<SavedFitting> = list().filter { it.active }

    private suspend fun saveAll(fittings: List<SavedFitting>) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(SettingsEntity(scope = SCOPE, overridesJson = json.encodeToString(serializer, fittings)))
    }

    /** Saves a freshly-parsed [ParsedFitting] under `name`, mirroring
     * `do_add_fitting`'s own shape (parse first, then persist the result) -
     * the caller ([DoctrineFittingsScreen]) already has a [ParsedFitting]
     * from the existing Parse button, this just adds the persistence step
     * that action always had on desktop. Returns the new [SavedFitting]. */
    suspend fun save(
        name: String,
        rawEft: String,
        parsed: ParsedFitting,
        doctrineId: String = "",
        contractTarget: Int = 0,
        stockpileTarget: Int = 0,
        cargoTolerancePct: Double? = null,
    ): SavedFitting = writeMutex.withLock {
        val fitting = SavedFitting(
            fittingId = UUID.randomUUID().toString(),
            name = name.ifBlank { parsed.fitName },
            doctrineId = doctrineId,
            hullTypeId = parsed.hullTypeId,
            hullName = parsed.hullName,
            rawEft = rawEft,
            items = parsed.items.map { SavedFittingItem(it.lineNo, it.slotSection, it.typeId, it.quantity, it.isOffline) },
            contractTarget = contractTarget,
            stockpileTarget = stockpileTarget,
            cargoTolerancePct = cargoTolerancePct,
            createdAt = Instant.now().toString(),
        )
        val current = list()
        saveAll(current + fitting)
        fitting
    }

    suspend fun delete(fittingId: String) = writeMutex.withLock {
        saveAll(list().filterNot { it.fittingId == fittingId })
    }

    /** Updates a saved fitting's targets/active flag in place - mirrors the
     * subset of `do_update_fitting` this platform has a use for (no
     * re-parse-on-edit here: editing the raw EFT text is not wired to this
     * screen's Save flow yet, only targets/active are). */
    suspend fun updateTargets(
        fittingId: String,
        contractTarget: Int? = null,
        stockpileTarget: Int? = null,
        active: Boolean? = null,
    ) = writeMutex.withLock {
        val current = list()
        val updated = current.map { f ->
            if (f.fittingId != fittingId) f
            else f.copy(
                contractTarget = contractTarget ?: f.contractTarget,
                stockpileTarget = stockpileTarget ?: f.stockpileTarget,
                active = active ?: f.active,
            )
        }
        saveAll(updated)
    }
}
