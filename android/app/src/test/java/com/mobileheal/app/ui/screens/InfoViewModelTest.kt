package com.mobileheal.app.ui.screens

import androidx.lifecycle.SavedStateHandle
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.repository.RulesRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class InfoViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private fun repo(rules: AppRules) = object : RulesRepository {
        private val flow = MutableStateFlow(rules)
        override val rules: StateFlow<AppRules> = flow
        override fun update(rules: AppRules) { flow.value = rules }
    }

    @Test fun `custom screen reads title and message from the rules`() {
        val rules = AppRules(emptyList(), mapOf("screen.thank_you.title" to "Thank You", "screen.thank_you.message" to "See you soon"))
        val vm = InfoViewModel(SavedStateHandle(mapOf("id" to "thank_you")), repo(rules))
        assertEquals("Thank You", vm.state.value.spec.title)
        assertEquals("See you soon", vm.state.value.spec.message)
    }

    @Test fun `missing id shows the success screen defaults`() {
        val vm = InfoViewModel(SavedStateHandle(), repo(AppRules.EMPTY))
        assertEquals("Profile saved", vm.state.value.spec.title)
    }
}
