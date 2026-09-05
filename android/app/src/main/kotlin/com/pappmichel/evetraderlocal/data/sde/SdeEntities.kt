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
 * Seven of the desktop build's twelve SDE tables are modelled here, and that
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
    /** The whole-batch unit reprocessing rounds down to before applying
     * yield% (e.g. Veldspar=100) - added alongside `sde_type_materials` for
     * the Ore & Minerals / Reprocessing Quote port (see
     * `data/refining/ReprocessingYield.kt`). Already present in
     * `invTypes.csv` itself, same column sde.py's own `types_rows` reads -
     * no separate fetch needed, unlike the materials table below. Null for
     * every type that isn't reprocessable at all (ships, skillbooks, BPOs/
     * BPCs), which `applyReprocessingYield` treats as "nothing to
     * reprocess", not an error. */
    val portionSize: Int? = null,
)

/** One `invTypeMaterials.csv` row: reprocessing `typeId` yields `quantity`
 * units of `materialTypeId` per whole portion (`SdeTypeEntity.portionSize`),
 * before any yield% is applied. Added for the Ore & Minerals / Reprocessing
 * Quote port - the desktop build's `sde_type_materials` table
 * (storage.py), out of scope until this feature needed it (see
 * SdeRepository's own docstring on "port only what's used").
 *
 * A composite primary key over both columns (rather than a synthetic
 * auto-increment id this table has no use for) reflects the real shape of
 * the data - one type reprocesses into several materials - and happens to
 * reject the one malformed input this table could ever see (two rows
 * disagreeing about the same type/material pair) as a constraint violation
 * instead of silently keeping one and dropping the other. */
@Entity(tableName = "sde_type_materials", primaryKeys = ["typeId", "materialTypeId"], indices = [Index("typeId")])
data class SdeTypeMaterialEntity(
    val typeId: Int,
    val materialTypeId: Int,
    val quantity: Double,
)

/** One `industryActivityMaterials.csv` row, Manufacturing (activityId=1) OR
 * Invention (activityId=8) - see `SdeFile.BLUEPRINT_MATERIALS`'s own comment
 * for why Reaction/Copying rows are still never fetched at all: one run of
 * `blueprintTypeId` (for activityId=1) or one invention attempt with it (for
 * activityId=8, where the "materials" consumed are the datacores) requires
 * `quantity` units of `materialTypeId`. The desktop build's
 * `sde_blueprint_materials` table (storage.py) carries every activity id;
 * this is a deliberately narrower slice, extended from Manufacturing-only to
 * also carry Invention for the Invention Estimator port (see
 * `data/production/InventionEstimator.kt`) - Reaction and Copying still have
 * no ported consumer on Android, same "port only what's used" precedent
 * `SdeTypeMaterialEntity` set for the Reprocessing Quote port.
 *
 * Composite primary key over (blueprintTypeId, activityId, materialTypeId) -
 * `activityId` joined the key in this same change (it used to be
 * (blueprintTypeId, materialTypeId) only, back when this table held
 * Manufacturing rows exclusively and every blueprintTypeId only ever
 * appeared under one activity): a genuine T1 blueprint has BOTH a
 * Manufacturing formula for the item it builds AND an Invention formula for
 * the datacores an attempt consumes, and there is no SDE guarantee the two
 * activities' material lists never share a `materialTypeId` - without
 * `activityId` in the key, such a row would collide and silently drop one of
 * the two. A real formula (of either activity) never lists the same material
 * twice, so this still reflects the true shape of the data, just widened by
 * exactly the one column the new activity actually needs distinguished. */
@Entity(
    tableName = "sde_blueprint_materials",
    primaryKeys = ["blueprintTypeId", "activityId", "materialTypeId"],
    indices = [Index("blueprintTypeId")],
)
data class SdeBlueprintMaterialEntity(
    val blueprintTypeId: Int,
    val activityId: Int,
    val materialTypeId: Int,
    val quantity: Double,
)

/** One `industryActivityProducts.csv` row, Manufacturing (activityId=1) OR
 * Invention (activityId=8) - see `SdeBlueprintMaterialEntity`'s docstring for
 * why both activities are carried here now: running `blueprintTypeId`
 * produces `quantity` units of `productTypeId` per run (Manufacturing), or
 * one successful invention attempt with it produces `quantity` runs of the
 * resulting T2/T3 BPC `productTypeId` (Invention). The counterpart of
 * `storage.get_blueprint_for_product`/`get_invention_recipe`'s source table -
 * `SdeDao.blueprintForProduct` (Manufacturing) and `SdeDao.inventionProduct`
 * (Invention) are the two lookups this table now backs.
 *
 * Composite primary key over (blueprintTypeId, activityId, productTypeId) -
 * `activityId` joined the key for the same reason
 * `SdeBlueprintMaterialEntity`'s own docstring gives: a real blueprint has
 * exactly one product per run *per activity*, but a T1 blueprint's
 * Manufacturing product (the T1 item itself) and its Invention product (the
 * T2/T3 BPC) are two different rows that must not collide. */
@Entity(
    tableName = "sde_blueprint_products",
    primaryKeys = ["blueprintTypeId", "activityId", "productTypeId"],
    indices = [Index("productTypeId")],
)
data class SdeBlueprintProductEntity(
    val blueprintTypeId: Int,
    val activityId: Int,
    val productTypeId: Int,
    val quantity: Double,
)

/** One `industryActivityProbabilities.csv` row, Invention (activityId=8)
 * only - the SDE never lists a probability for any other activity, matching
 * `sde.py`'s own `activity_id == ACTIVITY_INVENTION` filter exactly:
 * attempting invention with `t1BlueprintTypeId` (a real T1 blueprint for
 * Tech II, a Sleeper relic for Tech III - see
 * `InventionEstimator.kt`'s own module docstring) against `productTypeId`
 * succeeds at `probability` (already folded in EVE's own base rate; skill
 * and decryptor bonuses are applied on top by `InventionEstimator.
 * skillMultiplier`/decryptor lookup, never baked into this table).
 * `productTypeId` is part of the key (not just `t1BlueprintTypeId`) purely
 * for symmetry with `storage.get_invention_recipe`'s own two-column lookup;
 * in practice one T1 blueprint/relic only ever invents one product. The
 * counterpart of desktop's `sde_invention_probability` table - added
 * entirely new for the Invention Estimator port, no prior narrower version
 * of this table existed on Android (unlike the two tables above, which grew
 * an activity rather than being created from nothing). */
@Entity(
    tableName = "sde_invention_probability",
    primaryKeys = ["t1BlueprintTypeId", "productTypeId"],
)
data class SdeInventionProbabilityEntity(
    val t1BlueprintTypeId: Int,
    val productTypeId: Int,
    val probability: Double,
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
    @Insert suspend fun insertTypeMaterials(rows: List<SdeTypeMaterialEntity>)
    @Insert suspend fun insertBlueprintMaterials(rows: List<SdeBlueprintMaterialEntity>)
    @Insert suspend fun insertBlueprintProducts(rows: List<SdeBlueprintProductEntity>)
    @Insert suspend fun insertInventionProbability(rows: List<SdeInventionProbabilityEntity>)

    @Query("DELETE FROM sde_types") suspend fun clearTypes()
    @Query("DELETE FROM sde_groups") suspend fun clearGroups()
    @Query("DELETE FROM sde_categories") suspend fun clearCategories()
    @Query("DELETE FROM sde_market_groups") suspend fun clearMarketGroups()
    @Query("DELETE FROM sde_solar_systems") suspend fun clearSolarSystems()
    @Query("DELETE FROM sde_stations") suspend fun clearStations()
    @Query("DELETE FROM sde_type_materials") suspend fun clearTypeMaterials()
    @Query("DELETE FROM sde_blueprint_materials") suspend fun clearBlueprintMaterials()
    @Query("DELETE FROM sde_blueprint_products") suspend fun clearBlueprintProducts()
    @Query("DELETE FROM sde_invention_probability") suspend fun clearInventionProbability()

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

    /** Every published type with a cached Manufacturing blueprint that
     * produces it - Build Candidates' whole-catalog scan universe (the
     * Android counterpart of desktop's `storage.load_sde_types_with_
     * market_group` + `classify_activity(type_id) is not None` filter,
     * narrowed the same "Manufacturing only" way every other query on this
     * cache already is - see `SdeBlueprintProductEntity`'s own docstring).
     * `p.activityId = 1` is now a real filter rather than a given: this
     * table also carries Invention (8) rows since the Invention Estimator
     * port, and a T2/T3 item's *Invention* product row must not make it look
     * Manufacturing-buildable here. `DISTINCT` guards a type whose product
     * happens to appear in more than one blueprint row (shouldn't happen in
     * real SDE data, but costs nothing to guard); unpublished types
     * (removed/never-released items) are excluded the same way
     * `oreIceCandidateTypes` excludes them. */
    @Query(
        "SELECT DISTINCT t.* FROM sde_types t " +
            "JOIN sde_blueprint_products p ON p.productTypeId = t.typeId " +
            "WHERE p.activityId = 1 AND t.published = 1"
    )
    suspend fun manufacturableTypes(): List<SdeTypeEntity>

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

    /** Exact, case-insensitive name -> type id, the same match rule
     * quote.py's `resolve_type_id` uses (a paste line's own name field
     * should already be a real EVE item name, so a fuzzy/substring match
     * risks silently resolving to the wrong item) - `COLLATE NOCASE` does
     * the case folding in SQL rather than pulling candidates into Kotlin to
     * compare, since (unlike desktop's `search_sde_types`, which also
     * powers a type-ahead search box) nothing here needs the substring
     * candidates for anything else. `LIMIT 1` is safe: real EVE item names
     * are unique regardless of case, so more than one exact match would
     * mean a corrupted cache, not a genuine ambiguity to disambiguate. */
    @Query("SELECT typeId FROM sde_types WHERE typeName = :name COLLATE NOCASE LIMIT 1")
    suspend fun resolveTypeIdByName(name: String): Int?

    /** `SdeTypeEntity.portionSize` for one type - see that field's own
     * docstring. Null for a type with no portion size at all (not yet
     * SDE-refreshed, or genuinely not reprocessable). */
    @Query("SELECT portionSize FROM sde_types WHERE typeId = :typeId")
    suspend fun portionSize(typeId: Int): Int?

    /** Every material `typeId` reprocesses into, one whole portion's worth
     * each - the counterpart of storage.py's `get_type_materials`. Empty for
     * a type with no material rows at all (same two reasons as
     * `portionSize` above). */
    @Query("SELECT materialTypeId, quantity FROM sde_type_materials WHERE typeId = :typeId")
    suspend fun typeMaterials(typeId: Int): List<TypeMaterialRow>

    /** Every published compressed ore/ice type - the Ore Shortlist's fixed,
     * SDE-derived candidate universe (Ore & Minerals / Ore Shortlist, see
     * `data/refining/OreShortlist.kt`'s `buildOreCandidateUniverse`), the
     * Android counterpart of storage.py's `load_ore_ice_candidate_types`.
     * Category id 25 is Ore (mirrors `refining.constants.
     * ORE_ICE_CATEGORY_ID` as a bare literal here, same "this file doesn't
     * import a submodule's own constants" reasoning as `resolveTypeByName`'s
     * own category filter). Filters on `typeName LIKE 'Compressed%'`, not
     * group name - confirmed against a real Fuzzwork-fetched SDE that a
     * compressed ore/ice type shares its *raw* ore's own group ("Compressed
     * Veldspar" lives in group "Veldspar" alongside raw "Veldspar" itself) -
     * there is no dedicated "Compressed <Family>" group in the real data, so
     * filtering on group name instead would silently produce an empty
     * candidate universe. */
    @Query(
        "SELECT t.typeId AS typeId, t.typeName AS typeName, t.volume AS volume, g.groupName AS groupName " +
            "FROM sde_types t JOIN sde_groups g ON g.groupId = t.groupId " +
            "WHERE g.categoryId = 25 AND t.typeName LIKE 'Compressed%' AND t.published = 1"
    )
    suspend fun oreIceCandidateTypes(): List<OreIceCandidateTypeRow>

    /** "Which blueprint makes this item, and how many per run" - the Android
     * counterpart of `storage.get_blueprint_for_product`, narrowed to
     * Manufacturing (`activityId = 1`) explicitly now that this table also
     * carries Invention rows (see `SdeBlueprintProductEntity`'s docstring) -
     * before that change the table only ever held Manufacturing rows, so the
     * filter was implicit; it must be explicit now, or a T2/T3 item's
     * Invention product row (same `productTypeId`, a T1 blueprint/relic's
     * `blueprintTypeId`) could win the `LIMIT 1` instead of its real
     * Manufacturing blueprint. No `activityId` preference ordering is needed
     * beyond that single equality filter, unlike the desktop query's own
     * `ORDER BY activity_id`. `LIMIT 1` is a defensive no-op in practice (a
     * real product has exactly one Manufacturing blueprint), same reasoning
     * as `resolveTypeByName`'s own LIMIT 1. Null for a type with no
     * Manufacturing blueprint at all - a raw material, or a type this cache
     * hasn't been refreshed to know how to build. */
    @Query(
        "SELECT blueprintTypeId, quantity FROM sde_blueprint_products " +
            "WHERE productTypeId = :productTypeId AND activityId = 1 LIMIT 1"
    )
    suspend fun blueprintForProduct(productTypeId: Int): SdeBlueprintForProductRow?

    /** One run's Manufacturing materials at ME 0, before any ME reduction -
     * applying ME is the caller's job, matching `storage.
     * get_blueprint_materials`'s own contract exactly. `activityId = 1` is
     * now a real filter (see `blueprintForProduct`'s own doc for why): a
     * blueprint that is also a genuine T1 Invention source has its datacore
     * rows (`inventionMaterials`, activityId=8) stored under the same
     * `blueprintTypeId` in this same table, and this query must not return
     * those. Empty (not null) for a blueprint id this cache has no
     * Manufacturing material rows for. */
    @Query(
        "SELECT materialTypeId, quantity FROM sde_blueprint_materials " +
            "WHERE blueprintTypeId = :blueprintTypeId AND activityId = 1"
    )
    suspend fun blueprintMaterials(blueprintTypeId: Int): List<TypeMaterialRow>

    // -------------------------------------------------------- invention
    // Backs InventionEstimator.kt (data/production/InventionEstimator.kt),
    // the Android counterpart of storage.py's get_invention_recipe/
    // find_invention_recipe_candidates_by_product_type_id/
    // find_invention_recipe_by_product_name. All three query the same two
    // tables this port's Invention Estimator addition widened
    // (sde_blueprint_materials/products, now activityId=8 too) plus the
    // brand new sde_invention_probability table.

    /** Invention (activity 8) "product" row for one T1 blueprint/relic: the
     * T2/T3 blueprint it can invent, and the base run count (before any
     * decryptor bonus) a success produces. Null for a type that invents
     * nothing at all (a real T1 item never invention-sourced, or a plain
     * Manufacturing-only blueprint). Mirrors the `product_type_id`/
     * `base_runs` half of `storage.get_invention_recipe`'s dict. */
    @Query(
        "SELECT productTypeId, quantity FROM sde_blueprint_products " +
            "WHERE blueprintTypeId = :t1BlueprintTypeId AND activityId = 8"
    )
    suspend fun inventionProduct(t1BlueprintTypeId: Int): InventionProductRow?

    /** The datacores one invention attempt with `t1BlueprintTypeId`
     * consumes - activity 8's "materials" are datacores, not build
     * materials (see `SdeBlueprintMaterialEntity`'s docstring). Mirrors the
     * `datacores` half of `storage.get_invention_recipe`'s dict. Empty for a
     * type with no cached Invention material rows. */
    @Query(
        "SELECT materialTypeId, quantity FROM sde_blueprint_materials " +
            "WHERE blueprintTypeId = :t1BlueprintTypeId AND activityId = 8"
    )
    suspend fun inventionMaterials(t1BlueprintTypeId: Int): List<TypeMaterialRow>

    /** EVE's base (pre-skill, pre-decryptor) success probability for one
     * (t1 blueprint/relic, product) invention pair - null when the SDE has
     * the recipe (an `inventionProduct` row exists) but no probability row
     * for it, which callers must treat as "can't estimate", never as 0 -
     * mirrors `storage.get_invention_recipe`'s own `base_probability`
     * contract exactly. */
    @Query(
        "SELECT probability FROM sde_invention_probability " +
            "WHERE t1BlueprintTypeId = :t1BlueprintTypeId AND productTypeId = :productTypeId"
    )
    suspend fun inventionProbability(t1BlueprintTypeId: Int, productTypeId: Int): Double?

    /** Every valid invention source (a T1 blueprint for Tech II, a Sleeper
     * relic for Tech III) for `productBlueprintTypeId`, best-probability-
     * first - the Android counterpart of `storage.
     * find_invention_recipe_candidates_by_product_type_id`. Empty for a type
     * that isn't an invented product at all (a T1 item, or a BPO never
     * invention-sourced) - this emptiness is what a caller uses to tell
     * "not invented" apart from "invented but unpriceable". A `LEFT JOIN`
     * (not `INNER`) keeps a candidate with an Invention product row but no
     * probability row in the list (ordered last via `probability DESC`
     * putting SQL NULL after every real value) rather than silently hiding
     * it, matching the desktop query's own `LEFT JOIN` exactly - Tech III's
     * up to three relic grades (Intact/Malfunctioning/Wrecked) is the real
     * reason this can return more than one row; see
     * `InventionEstimator.kt`'s module docstring. */
    @Query(
        "SELECT p.blueprintTypeId FROM sde_blueprint_products p " +
            "LEFT JOIN sde_invention_probability prob " +
            "  ON prob.t1BlueprintTypeId = p.blueprintTypeId " +
            "  AND prob.productTypeId = p.productTypeId " +
            "WHERE p.activityId = 8 AND p.productTypeId = :productBlueprintTypeId " +
            "ORDER BY prob.probability DESC, p.blueprintTypeId"
    )
    suspend fun inventionRecipeCandidates(productBlueprintTypeId: Int): List<Int>

    /** Given a T2/T3 blueprint's *name* (what the Invention Estimator screen
     * takes as input, e.g. "Damage Control II Blueprint"), the invented
     * product's own `productTypeId` - the Android counterpart of the
     * `product_type_id` half of `storage.find_invention_recipe_by_product_
     * name`'s returned pair (this port never needs that function's other
     * half, an arbitrary single `t1_blueprint_type_id` - see that function's
     * own docstring on why using it for anything but resolving the name
     * would be a real, previously-confirmed bug). Case-insensitive,
     * `COLLATE NOCASE` matching every other exact type-name lookup in this
     * file. `LIMIT 1` is a defensive no-op (real EVE item names are unique
     * regardless of case). Null when `productName` isn't a real invented
     * blueprint's name at all. */
    @Query(
        "SELECT p.productTypeId FROM sde_blueprint_products p " +
            "JOIN sde_types t ON t.typeId = p.productTypeId " +
            "WHERE p.activityId = 8 AND t.typeName = :productName COLLATE NOCASE LIMIT 1"
    )
    suspend fun resolveInventionProductTypeId(productName: String): Int?

    /** A type's SDE category id (type -> group -> category), e.g. to tell a
     * genuine T1 blueprint (category 9) apart from a Tech III Sleeper relic
     * (category 34, `ANCIENT_RELIC_CATEGORY_ID` in `InventionEstimator.kt`) -
     * the two invention sources that must be priced differently (see that
     * file's own module docstring). Distinct from `categoryNameFor`, which
     * returns the *name* for display; invention pricing needs the numeric
     * id to compare against the constant. */
    @Query(
        "SELECT g.categoryId FROM sde_types t " +
            "JOIN sde_groups g ON g.groupId = t.groupId " +
            "WHERE t.typeId = :typeId"
    )
    suspend fun categoryIdFor(typeId: Int): Int?

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
    @Query("SELECT COUNT(*) FROM sde_type_materials") suspend fun countTypeMaterials(): Int
    @Query("SELECT COUNT(*) FROM sde_blueprint_materials") suspend fun countBlueprintMaterials(): Int
    @Query("SELECT COUNT(*) FROM sde_blueprint_products") suspend fun countBlueprintProducts(): Int
    @Query("SELECT COUNT(*) FROM sde_invention_probability") suspend fun countInventionProbability(): Int
}

/** Room's projection for `SdeDao.blueprintForProduct` - mirrors the
 * (blueprint_type_id, quantity) half of `storage.get_blueprint_for_product`'s
 * tuple that a Manufacturing-only lookup still needs (its `activity_id` is
 * always 1 here, so unlike the desktop tuple this doesn't need to carry it). */
data class SdeBlueprintForProductRow(val blueprintTypeId: Int, val quantity: Double)

/** Room's projection for `SdeDao.inventionProduct` - the T2/T3 blueprint one
 * invention attempt targets, and its base (pre-decryptor) run count. */
data class InventionProductRow(val productTypeId: Int, val quantity: Double)

/** Room's projection shape for `SdeDao.typeMaterials` - a `data class` (not
 * the entity itself) because the query selects two of its three columns,
 * and Room maps a `@Query` result positionally/by-name onto whatever type is
 * asked for, entity or not. */
data class TypeMaterialRow(val materialTypeId: Int, val quantity: Double)

/** Room's projection shape for `SdeDao.oreIceCandidateTypes` - see that
 * query's own docstring. `groupName` is nullable because Room maps a
 * possibly-null joined column that way even though the query's own WHERE
 * clause never actually lets one through with a null group. */
data class OreIceCandidateTypeRow(
    val typeId: Int,
    val typeName: String?,
    val volume: Double?,
    val groupName: String?,
)
