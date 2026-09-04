package com.pappmichel.evetraderlocal.data.sde

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Index
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert

/** The on-device half of the SDE cache: Room entities mirroring the desktop
 * build's `sde_*` SQLite tables (storage.py's schema block), plus the DAO the
 * refresh and the lookups go through.
 *
 * Six of the desktop build's twelve SDE tables are modelled here, and that
 * gap is deliberate - see SdeRepository's docstring for the full "port only
 * what is used" reasoning and the list of what is intentionally absent.
 *
 * Two shape differences from storage.py, both intentional:
 *  - Column order is *not* load-bearing here the way that schema's own
 *    comment says it is on desktop. `replace_sde_data` inserts positionally
 *    from tuples, so a new desktop column has to go at the end; Room inserts
 *    from named entity properties, so a new field can go anywhere.
 *  - Ids that can be compared against a live ESI `location_id` (station,
 *    solar system, region) are `Long`, not `Int`. Every one of them fits in
 *    32 bits today, but ESI hands back player-owned structure ids well past
 *    `Int.MAX_VALUE` in that same field, and the whole point of
 *    `stationIdsInRegion` is to be compared against exactly those values -
 *    a widening conversion at every call site is a worse trade than four
 *    extra bytes a row. Type/group/category/market-group ids stay `Int`,
 *    matching EsiClient's own type ids. */
@Entity(tableName = "sde_types", indices = [Index("groupId")])
data class SdeTypeEntity(
    @PrimaryKey val typeId: Int,
    val groupId: Int?,
    val typeName: String?,
    val volume: Double?,
    /** 1/0, not a Boolean - the SDE's own `published` flag, stored exactly as
     * sde.py stores it. Unpublished types still exist in the dump (removed or
     * never-released items) and a caller filtering candidates wants to see
     * the flag rather than have this table silently drop those rows. */
    val published: Int,
    val marketGroupId: Int?,
    val metaLevel: Int?,
)

@Entity(tableName = "sde_groups")
data class SdeGroupEntity(
    @PrimaryKey val groupId: Int,
    val categoryId: Int?,
    val groupName: String?,
)

@Entity(tableName = "sde_categories")
data class SdeCategoryEntity(
    @PrimaryKey val categoryId: Int,
    val categoryName: String?,
)

@Entity(tableName = "sde_market_groups")
data class SdeMarketGroupEntity(
    @PrimaryKey val marketGroupId: Int,
    val parentGroupId: Int?,
    val marketGroupName: String?,
)

@Entity(tableName = "sde_solar_systems")
data class SdeSolarSystemEntity(
    @PrimaryKey val solarSystemId: Long,
    val solarSystemName: String?,
    val security: Double?,
    val regionId: Long?,
)

/** NPC stations only - `staStations.csv` is the SDE's static station list and
 * knows nothing about player-owned structures (those only exist through
 * authenticated ESI, and the desktop build treats them the same way). The
 * index on `solarSystemId` is what makes the region join in
 * `stationIdsInRegion` cheap; without it that query is a full scan of ~5000
 * stations per call. */
@Entity(tableName = "sde_stations", indices = [Index("solarSystemId")])
data class SdeStationEntity(
    @PrimaryKey val stationId: Long,
    val solarSystemId: Long,
    val stationName: String?,
)

/** The single-row bookkeeping table behind `get_sde_refresh_state` /
 * `set_sde_refresh_state` on desktop: when the cache was last replaced, and
 * the Fuzzwork ETag observed at that moment (see SdeDownloader for what the
 * ETag is actually used for). Pinned to `id = 1` for the same reason the
 * desktop table is - there is exactly one SDE cache per install, and a
 * primary key makes "insert or overwrite" a single upsert instead of a
 * read-then-branch. */
@Entity(tableName = "sde_refresh_state")
data class SdeRefreshStateEntity(
    @PrimaryKey val id: Int = 1,
    val refreshedAt: String,
    val dumpEtag: String?,
)

/** Room's projection for `SdeDao.resolveTypeByName` - not a table, just the
 * shape of that one join query's result columns. Mirrors the tuple
 * `storage.resolve_sde_type_by_name` returns on desktop (type_id, group_id,
 * category_id, meta_group_id, meta_level, type_name), minus `metaGroupId`
 * (no `invMetaTypes.csv` in this cache - see `DoctrineSdeResolver`'s
 * docstring). */
data class SdeTypeNameResolution(
    val typeId: Int,
    val groupId: Int?,
    val categoryId: Int?,
    val metaLevel: Int?,
    val typeName: String?,
)

@Dao
interface SdeDao {
    // ---------------------------------------------------------- refresh
    // Insert-only, in bulk. The deletes and the inserts are driven together
    // from SdeRepository.replaceAll inside one transaction - see there for
    // why the whole thing has to be atomic.

    @Insert suspend fun insertTypes(rows: List<SdeTypeEntity>)
    @Insert suspend fun insertGroups(rows: List<SdeGroupEntity>)
    @Insert suspend fun insertCategories(rows: List<SdeCategoryEntity>)
    @Insert suspend fun insertMarketGroups(rows: List<SdeMarketGroupEntity>)
    @Insert suspend fun insertSolarSystems(rows: List<SdeSolarSystemEntity>)
    @Insert suspend fun insertStations(rows: List<SdeStationEntity>)

    @Query("DELETE FROM sde_types") suspend fun clearTypes()
    @Query("DELETE FROM sde_groups") suspend fun clearGroups()
    @Query("DELETE FROM sde_categories") suspend fun clearCategories()
    @Query("DELETE FROM sde_market_groups") suspend fun clearMarketGroups()
    @Query("DELETE FROM sde_solar_systems") suspend fun clearSolarSystems()
    @Query("DELETE FROM sde_stations") suspend fun clearStations()

    // ------------------------------------------------------ refresh state
    @Upsert suspend fun upsertRefreshState(state: SdeRefreshStateEntity)

    @Query("SELECT * FROM sde_refresh_state WHERE id = 1")
    suspend fun refreshState(): SdeRefreshStateEntity?

    // ---------------------------------------------------- bulk reads
    // Whole-table reads for CandidateDiscovery.buildCandidateUniverseFromSde
    // - unlike the single-row lookups below, that caller needs the *entire*
    // market-group tree and type table in memory at once to walk it, the
    // same shape storage.py's own load_sde_market_groups/
    // load_sde_types_with_market_group/load_sde_category_names give the
    // desktop build's build_candidate_universe.
    @Query("SELECT * FROM sde_market_groups") suspend fun allMarketGroups(): List<SdeMarketGroupEntity>
    @Query("SELECT * FROM sde_types WHERE marketGroupId IS NOT NULL") suspend fun typesWithMarketGroup(): List<SdeTypeEntity>
    @Query("SELECT * FROM sde_groups") suspend fun allGroups(): List<SdeGroupEntity>
    @Query("SELECT * FROM sde_categories") suspend fun allCategories(): List<SdeCategoryEntity>

    // ----------------------------------------------------------- lookups
    @Query("SELECT typeName FROM sde_types WHERE typeId = :typeId")
    suspend fun typeName(typeId: Int): String?

    @Query("SELECT * FROM sde_types WHERE typeId = :typeId")
    suspend fun type(typeId: Int): SdeTypeEntity?

    /** The group -> category join behind `categoryNameFor`. Kept as one SQL
     * statement rather than two round-trips because it is the exact lookup a
     * candidate-classification loop would run per type. */
    @Query(
        "SELECT c.categoryName FROM sde_types t " +
            "JOIN sde_groups g ON g.groupId = t.groupId " +
            "JOIN sde_categories c ON c.categoryId = g.categoryId " +
            "WHERE t.typeId = :typeId"
    )
    suspend fun categoryNameFor(typeId: Int): String?

    /** Every NPC station in a region, via the solar-system table - the direct
     * counterpart of storage.py's `get_station_ids_in_region`. `staStations`
     * has no region column of its own, which is precisely why
     * `mapSolarSystems.csv` is in this port's scope at all. */
    @Query(
        "SELECT s.stationId FROM sde_stations s " +
            "JOIN sde_solar_systems sys ON sys.solarSystemId = s.solarSystemId " +
            "WHERE sys.regionId = :regionId"
    )
    suspend fun stationIdsInRegion(regionId: Long): List<Long>

    /** Narrower counterpart of the above, mirroring
     * `get_station_ids_in_system` - "is this asset sitting in Jita itself"
     * rather than "anywhere in The Forge". */
    @Query("SELECT stationId FROM sde_stations WHERE solarSystemId = :systemId")
    suspend fun stationIdsInSystem(systemId: Long): List<Long>

    /** Exact, case-insensitive type-name lookup with its group's category
     * joined in - the Android counterpart of `storage.
     * resolve_sde_type_by_name`, used by the Doctrine EFT parser
     * (`data/doctrine/DoctrineSdeResolver.kt`) to turn a pasted fitting
     * line's item name into a type id. `COLLATE NOCASE` matches SQLite's
     * default text comparison on the desktop build's own equivalent query
     * (SQLite's `=` there is already case-insensitive for ASCII by default
     * in that schema) - deliberately exact, never a substring/fuzzy match,
     * per parser.py's own "no fuzzy matching in real resolution, ever"
     * rule. `LIMIT 1` is a defensive no-op in practice (type names are
     * unique in the SDE) but keeps this a single-row query by contract. */
    @Query(
        "SELECT t.typeId, t.groupId, g.categoryId, t.metaLevel, t.typeName FROM sde_types t " +
            "LEFT JOIN sde_groups g ON g.groupId = t.groupId " +
            "WHERE t.typeName = :name COLLATE NOCASE LIMIT 1"
    )
    suspend fun resolveTypeByName(name: String): SdeTypeNameResolution?

    /** Every published Ship/Structure type name (SDE category ids 6 and 65 -
     * matching `doctrine/constants.py`'s `VALID_HULL_CATEGORY_IDS`) - the
     * Android counterpart of `storage.list_hull_type_names`, used only for
     * the parser's "did you mean" hull-name suggestion (Phase 3 A.7). */
    @Query(
        "SELECT t.typeName FROM sde_types t " +
            "JOIN sde_groups g ON g.groupId = t.groupId " +
            "WHERE g.categoryId IN (6, 65) AND t.typeName IS NOT NULL"
    )
    suspend fun hullTypeNames(): List<String>

    // ------------------------------------------------------- row counts
    // The counterpart of storage.py's `sde_row_counts`: what the UI prints
    // after a refresh, and the cheapest way to tell an empty cache from a
    // populated one.
    @Query("SELECT COUNT(*) FROM sde_types") suspend fun countTypes(): Int
    @Query("SELECT COUNT(*) FROM sde_groups") suspend fun countGroups(): Int
    @Query("SELECT COUNT(*) FROM sde_categories") suspend fun countCategories(): Int
    @Query("SELECT COUNT(*) FROM sde_market_groups") suspend fun countMarketGroups(): Int
    @Query("SELECT COUNT(*) FROM sde_solar_systems") suspend fun countSolarSystems(): Int
    @Query("SELECT COUNT(*) FROM sde_stations") suspend fun countStations(): Int
}
