package com.mobileheal.app.ui.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.mobileheal.app.generated.generatedDestinations
import com.mobileheal.app.ui.screens.InfoRoute

object Routes {
    const val LOGIN = "screen/login"
    const val ORDER = "screen/order"
    const val SCREEN_PATTERN = "screen/{id}"
    fun screen(id: String) = "screen/$id"
}

@Composable
fun AppNavHost(navController: NavHostController = rememberNavController()) {
    val back: () -> Unit = { navController.popBackStack() }
    val navigate: (String) -> Unit = { route -> navController.navigate(route) }
    NavHost(navController = navController, startDestination = Routes.LOGIN) {
        // Screens written by the MobileHeal Android Developer Agent register exact routes ("screen/<id>"),
        // which take precedence over the rule-driven fallback below.
        generatedDestinations(onBack = back, onNavigate = navigate)
        composable(Routes.SCREEN_PATTERN, arguments = listOf(navArgument("id") { type = NavType.StringType })) {
            InfoRoute(onBack = back)
        }
    }
}
