package com.mobileheal.app.ui.order

import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.FieldRule
import com.mobileheal.domain.model.Profile
import com.mobileheal.domain.model.SaveOutcome
import com.mobileheal.domain.repository.RulesRepository
import com.mobileheal.domain.usecase.ResolveNavigationUseCase
import com.mobileheal.domain.usecase.SaveProfileUseCase
import com.mobileheal.domain.usecase.ValidateProfileUseCase
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

private class FakeRulesRepository(initial: AppRules) : RulesRepository {
    override val rules = MutableStateFlow(initial)
    override fun update(rules: AppRules) { this.rules.value = rules }
}

private class FakeProfileRepository(private val outcome: Result<SaveOutcome>) : com.mobileheal.domain.repository.ProfileRepository {
    override suspend fun load(id: Int) = Profile(id, emptyMap())
    override suspend fun save(profile: Profile): SaveOutcome = outcome.getOrThrow()
}

@OptIn(ExperimentalCoroutinesApi::class)
class OrderViewModelTest {

    private fun rules(): AppRules = AppRules(
        fields = listOf(
            FieldRule("name", true),
            FieldRule("email", true),
            FieldRule("restaurant_name", true),
            FieldRule("menu_item", true),
            FieldRule("quantity", true),
            FieldRule("fulfillment_method", true),
        ),
        ui = mapOf("after_save" to "success_screen")
    )

    @Test
    fun `submit navigates to success when no missing`() = runTest {
        val rr = FakeRulesRepository(rules())
        val outcome = Result.success(SaveOutcome(Profile(1, emptyMap()), emptyList()))
        val vm = OrderViewModel(
            rulesRepository = rr,
            validate = ValidateProfileUseCase(),
            saveUseCase = SaveProfileUseCase(FakeProfileRepository(outcome)),
            resolveNav = ResolveNavigationUseCase(),
            profileId = 1
        )
        // Fill all fields
        rr.rules.value.displayFields.forEach { f: String -> vm.onAction(OrderAction.FieldChanged(f, "x")) }
        vm.onAction(OrderAction.Submit)
        val effect = vm.effects.first()
        assertEquals(OrderEffect.NavigateTo("success"), effect)
    }

    @Test
    fun `submit shows missing when required not filled`() = runTest {
        val rr = FakeRulesRepository(rules())
        val outcome = Result.success(SaveOutcome(Profile(1, emptyMap()), listOf("name")))
        val vm = OrderViewModel(
            rulesRepository = rr,
            validate = ValidateProfileUseCase(),
            saveUseCase = SaveProfileUseCase(FakeProfileRepository(outcome)),
            resolveNav = ResolveNavigationUseCase(),
            profileId = 1
        )
        vm.onAction(OrderAction.Submit)
        val s = vm.state.value as OrderUiState.Content
        // local validation should already mark required
        assert(s.missing.contains("name"))
    }
}
