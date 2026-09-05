package com.pappmichel.evetraderlocal.data.doctrine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Contract-side [DoctrineValidation] functions, ported case-for-case from
 * the desktop build's `tests/test_doctrine_validation.py` (the
 * hull-gate/Ist/deviation/wrong-variant/score/status/ampel sections). */
class DoctrineContractMatchingTest {
    private val HULL = 999

    // --------------------------------------------------------------- hull gate / Ist
    @Test
    fun hullGate_requiresIncludedOnly_notSingleton() {
        assertTrue(DoctrineValidation.hullGateSatisfied(
            listOf(DoctrineValidation.ContractItemRow(1, 0, HULL, 1.0, true, true)), HULL))
        assertTrue(DoctrineValidation.hullGateSatisfied(
            listOf(DoctrineValidation.ContractItemRow(1, 0, HULL, 1.0, true, false)), HULL)) // isSingleton=false, still gates
        assertFalse(DoctrineValidation.hullGateSatisfied(
            listOf(DoctrineValidation.ContractItemRow(1, 0, HULL, 1.0, false, true)), HULL)) // not included
        assertFalse(DoctrineValidation.hullGateSatisfied(
            listOf(DoctrineValidation.ContractItemRow(1, 0, 111, 1.0, true, true)), HULL)) // wrong type
    }

    @Test
    fun buildContractIst_excludesHullAndNonIncludedItems() {
        val items = listOf(
            DoctrineValidation.ContractItemRow(1, 0, HULL, 1.0, true, true),
            DoctrineValidation.ContractItemRow(1, 1, 100, 1.0, true, false),
            DoctrineValidation.ContractItemRow(1, 2, 999999, 5.0, false, false), // requested "want", not included
        )
        val ist = DoctrineValidation.buildContractIst(items, HULL)
        assertEquals(mapOf(100 to 1.0), ist)
    }

    @Test
    fun buildContractIst_sumsSeparatelyStackedChargesRegardlessOfDamage() {
        // ESI's contract-items response has no damage field at all - several
        // separately-stacked lines of the same charge type_id must sum.
        val items = listOf(
            DoctrineValidation.ContractItemRow(1, 0, HULL, 1.0, true, true),
            DoctrineValidation.ContractItemRow(1, 1, 400, 1.0, true, false),
            DoctrineValidation.ContractItemRow(1, 2, 400, 1.0, true, false),
            DoctrineValidation.ContractItemRow(1, 3, 400, 16.0, true, false),
        )
        val ist = DoctrineValidation.buildContractIst(items, HULL)
        assertEquals(mapOf(400 to 18.0), ist)
    }

    // --------------------------------------------------------------- deviations (B.4)
    @Test
    fun exactClass_missingItem_isCritical() {
        val devs = DoctrineValidation.computeDeviations(1, mapOf(100 to 1.0), emptyMap(), emptyList(), HULL, 0.9)
        assertEquals(1, devs.size)
        assertEquals(DoctrineValidation.DEVIATION_KIND_MISSING, devs[0].kind)
        assertEquals(DoctrineValidation.SEVERITY_CRITICAL, devs[0].severity)
    }

    @Test
    fun exactClass_short_isAlwaysCritical_noTolerance() {
        val items = listOf(DoctrineValidation.ContractItemRow(1, 0, 100, 1.0, true, false))
        val devs = DoctrineValidation.computeDeviations(1, mapOf(100 to 2.0), emptyMap(), items, HULL, 0.5)
        assertEquals(DoctrineValidation.DEVIATION_KIND_SHORT, devs[0].kind)
        assertEquals(DoctrineValidation.SEVERITY_CRITICAL, devs[0].severity)
    }

    @Test
    fun consumeClass_withinTolerance_isTolerable() {
        val items = listOf(DoctrineValidation.ContractItemRow(1, 0, 300, 9.0, true, false))
        val devs = DoctrineValidation.computeDeviations(1, emptyMap(), mapOf(300 to 10.0), items, HULL, 0.9)
        assertEquals(1, devs.size)
        assertEquals(DoctrineValidation.DEVIATION_KIND_SHORT, devs[0].kind)
        assertEquals(DoctrineValidation.SEVERITY_TOLERABLE, devs[0].severity)
    }

    @Test
    fun consumeClass_belowTolerance_isCritical() {
        val items = listOf(DoctrineValidation.ContractItemRow(1, 0, 300, 5.0, true, false))
        val devs = DoctrineValidation.computeDeviations(1, emptyMap(), mapOf(300 to 10.0), items, HULL, 0.9)
        assertEquals(DoctrineValidation.SEVERITY_CRITICAL, devs[0].severity)
    }

    @Test
    fun consumeClass_fullyMissing_isCriticalNotTolerable() {
        val devs = DoctrineValidation.computeDeviations(1, emptyMap(), mapOf(300 to 10.0), emptyList(), HULL, 0.9)
        assertEquals(DoctrineValidation.DEVIATION_KIND_MISSING, devs[0].kind)
        assertEquals(DoctrineValidation.SEVERITY_CRITICAL, devs[0].severity)
    }

    @Test
    fun extraItem_isInfoByDefault() {
        val items = listOf(DoctrineValidation.ContractItemRow(1, 0, 500, 1.0, true, false))
        val devs = DoctrineValidation.computeDeviations(1, emptyMap(), emptyMap(), items, HULL, 0.9)
        assertEquals(DoctrineValidation.DEVIATION_KIND_EXTRA, devs[0].kind)
        assertEquals(DoctrineValidation.SEVERITY_INFO, devs[0].severity)
    }

    @Test
    fun extraItem_isTolerableUnderStrictExtras() {
        val items = listOf(DoctrineValidation.ContractItemRow(1, 0, 500, 1.0, true, false))
        val devs = DoctrineValidation.computeDeviations(1, emptyMap(), emptyMap(), items, HULL, 0.9, strictExtras = true)
        assertEquals(DoctrineValidation.SEVERITY_TOLERABLE, devs[0].severity)
    }

    @Test
    fun notIncludedItem_isExtraInfo_requestedConsideration() {
        val items = listOf(DoctrineValidation.ContractItemRow(1, 0, 700, 3.0, false, false))
        val devs = DoctrineValidation.computeDeviations(1, emptyMap(), emptyMap(), items, HULL, 0.9)
        assertEquals(DoctrineValidation.DEVIATION_KIND_EXTRA, devs[0].kind)
        assertEquals(0.0, devs[0].expectedQty, 0.0)
        assertEquals(3.0, devs[0].actualQty, 0.0)
    }

    @Test
    fun exactMatch_producesNoDeviation() {
        val items = listOf(DoctrineValidation.ContractItemRow(1, 0, 100, 1.0, true, false))
        val devs = DoctrineValidation.computeDeviations(1, mapOf(100 to 1.0), emptyMap(), items, HULL, 0.9)
        assertTrue(devs.isEmpty())
    }

    // --------------------------------------------------------------- wrong_variant (B.6)
    @Test
    fun wrongVariant_pairsSameGroupSameSlot() {
        val devs = listOf(
            DoctrineValidation.DeviationRow(1, 100, DoctrineValidation.DEVIATION_KIND_MISSING, DoctrineValidation.SEVERITY_CRITICAL, 1.0, 0.0),
            DoctrineValidation.DeviationRow(1, 200, DoctrineValidation.DEVIATION_KIND_EXTRA, DoctrineValidation.SEVERITY_INFO, 0.0, 1.0),
        )
        val groupIdOf: (Int) -> Int? = { mapOf(100 to 50, 200 to 50)[it] }
        val slotOf: (Int) -> String? = { mapOf(100 to "low", 200 to "low")[it] }
        val result = DoctrineValidation.pairWrongVariants(devs, groupIdOf, slotOf)
        val byType = result.associateBy { it.typeId }
        assertEquals(DoctrineValidation.DEVIATION_KIND_WRONG_VARIANT to DoctrineValidation.SEVERITY_CRITICAL, byType.getValue(100).kind to byType.getValue(100).severity)
        assertEquals(DoctrineValidation.DEVIATION_KIND_WRONG_VARIANT to DoctrineValidation.SEVERITY_INFO, byType.getValue(200).kind to byType.getValue(200).severity)
    }

    @Test
    fun wrongVariant_doesNotPairDifferentGroups() {
        val devs = listOf(
            DoctrineValidation.DeviationRow(1, 100, DoctrineValidation.DEVIATION_KIND_MISSING, DoctrineValidation.SEVERITY_CRITICAL, 1.0, 0.0),
            DoctrineValidation.DeviationRow(1, 200, DoctrineValidation.DEVIATION_KIND_EXTRA, DoctrineValidation.SEVERITY_INFO, 0.0, 1.0),
        )
        val groupIdOf: (Int) -> Int? = { mapOf(100 to 50, 200 to 99)[it] }
        val slotOf: (Int) -> String? = { "low" }
        val result = DoctrineValidation.pairWrongVariants(devs, groupIdOf, slotOf)
        val byType = result.associateBy { it.typeId }
        assertEquals(DoctrineValidation.DEVIATION_KIND_MISSING, byType.getValue(100).kind)
        assertEquals(DoctrineValidation.DEVIATION_KIND_EXTRA, byType.getValue(200).kind)
    }

    // --------------------------------------------------------------- status (B.7)
    @Test
    fun contractStatus_unmatchedWhenNotMatched() {
        assertEquals(DoctrineValidation.VALIDATION_UNMATCHED, DoctrineValidation.contractStatus(emptyList(), matched = false))
    }

    @Test
    fun contractStatus_validWithNoDeviations() {
        assertEquals(DoctrineValidation.VALIDATION_VALID, DoctrineValidation.contractStatus(emptyList(), matched = true))
    }

    @Test
    fun contractStatus_invalidWithAnyCritical() {
        val devs = listOf(DoctrineValidation.DeviationRow(1, 100, DoctrineValidation.DEVIATION_KIND_MISSING, DoctrineValidation.SEVERITY_CRITICAL))
        assertEquals(DoctrineValidation.VALIDATION_INVALID, DoctrineValidation.contractStatus(devs, matched = true))
    }

    @Test
    fun contractStatus_tolerableWithOnlyTolerable() {
        val devs = listOf(DoctrineValidation.DeviationRow(1, 100, DoctrineValidation.DEVIATION_KIND_SHORT, DoctrineValidation.SEVERITY_TOLERABLE))
        assertEquals(DoctrineValidation.VALIDATION_TOLERABLE, DoctrineValidation.contractStatus(devs, matched = true))
    }

    @Test
    fun contractStatus_validWithOnlyInfo() {
        val devs = listOf(DoctrineValidation.DeviationRow(1, 100, DoctrineValidation.DEVIATION_KIND_EXTRA, DoctrineValidation.SEVERITY_INFO))
        assertEquals(DoctrineValidation.VALIDATION_VALID, DoctrineValidation.contractStatus(devs, matched = true))
    }

    // --------------------------------------------------------------- matching score (B.5)
    @Test
    fun matchScore_perfectMatchIsOne() {
        val score = DoctrineValidation.matchScore(mapOf(100 to 1.0), mapOf(300 to 5.0), mapOf(100 to 1.0, 300 to 5.0))
        assertEquals(1.0, score, 0.0)
    }

    @Test
    fun matchScore_emptySollClassesScorePerfectly() {
        assertEquals(1.0, DoctrineValidation.matchScore(emptyMap(), emptyMap(), emptyMap()), 0.0)
    }

    @Test
    fun matchScore_partialOverlapBelowThreshold() {
        val exact = mapOf(100 to 1.0, 101 to 1.0, 102 to 1.0, 103 to 1.0)
        val score = DoctrineValidation.matchScore(exact, emptyMap(), mapOf(100 to 1.0))
        assertFalse(DoctrineValidation.clearsMatchThreshold(score))
    }

    // --------------------------------------------------------------- contract ampel (B.8)
    @Test
    fun contractAmpel_grayWhenNeverSynced() {
        assertEquals("gray", DoctrineValidation.contractAmpel(null, 2, 0, 0))
    }

    @Test
    fun contractAmpel_grayWhenNoTargetSet() {
        assertEquals("gray", DoctrineValidation.contractAmpel("2026-01-01", 0, 0, 0))
    }

    @Test
    fun contractAmpel_greenWhenTargetMet() {
        assertEquals("green", DoctrineValidation.contractAmpel("2026-01-01", 2, 2, 0))
    }

    @Test
    fun contractAmpel_yellowWhenPartiallyCovered() {
        assertEquals("yellow", DoctrineValidation.contractAmpel("2026-01-01", 2, 1, 0))
    }

    @Test
    fun contractAmpel_redWhenNothingViable() {
        assertEquals("red", DoctrineValidation.contractAmpel("2026-01-01", 2, 0, 0))
    }
}
