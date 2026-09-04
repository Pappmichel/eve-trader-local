package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.esi.EsiContract
import org.junit.Assert.assertEquals
import org.junit.Test

class ContractHistoryTest {
    private fun contract(id: Long, status: String) = EsiContract(contractId = id, type = "item_exchange", status = status)

    @Test
    fun finishedOnly_keepsOnlyTheThreeFinishedStatuses() {
        val contracts = listOf(
            contract(1, "outstanding"),
            contract(2, "finished"),
            contract(3, "finished_issuer"),
            contract(4, "finished_contractor"),
            contract(5, "cancelled"),
            contract(6, "in_progress"),
            contract(7, "rejected"),
        )
        val kept = ContractHistory.finishedOnly(contracts).map { it.contractId }
        assertEquals(listOf(2L, 3L, 4L), kept)
    }

    @Test
    fun finishedOnly_emptyInputIsEmpty() {
        assertEquals(emptyList<EsiContract>(), ContractHistory.finishedOnly(emptyList()))
    }
}
