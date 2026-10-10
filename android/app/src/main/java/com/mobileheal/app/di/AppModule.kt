package com.mobileheal.app.di

import com.mobileheal.app.BuildConfig
import com.mobileheal.app.generated.RulesDefaults
import com.mobileheal.app.platform.HealAlerts
import com.mobileheal.app.platform.HealNotifier
import com.mobileheal.data.remote.BaseUrl
import com.mobileheal.data.remote.ClientHeaders
import java.net.URLEncoder
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.model.FieldRule
import com.mobileheal.domain.repository.LiveUpdatesRepository
import com.mobileheal.domain.repository.ProfileRepository
import com.mobileheal.domain.usecase.AuthenticateUserUseCase
import com.mobileheal.domain.usecase.LoadProfileUseCase
import com.mobileheal.domain.usecase.ObserveLiveUpdatesUseCase
import com.mobileheal.domain.usecase.ResolveNavigationUseCase
import com.mobileheal.domain.usecase.SaveProfileUseCase
import com.mobileheal.domain.usecase.ValidateProfileUseCase
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Qualifier

@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class ProfileId

@Module
@InstallIn(SingletonComponent::class)
abstract class AppModule {
    @Binds abstract fun healAlerts(impl: HealNotifier): HealAlerts

    companion object {
        @Provides @BaseUrl fun baseUrl(): String = BuildConfig.BASE_URL
        @Provides @ProfileId fun profileId(): Int = BuildConfig.PROFILE_ID

        /** Identifies this build to the server, so the portal can show which app (and source folder) is connected. */
        @Provides @ClientHeaders fun clientHeaders(): Map<String, String> = mapOf(
            "X-MobileHeal-App" to "${BuildConfig.APPLICATION_ID} ${BuildConfig.VERSION_NAME}",
            "X-MobileHeal-Rules" to RulesDefaults.SPEC_VERSION,
            "X-MobileHeal-Workspace" to URLEncoder.encode(BuildConfig.SOURCE_FOLDER, "UTF-8"),
        )

        /** Build-time demo rules for Restaurant Ordering; replaced live by CONFIG_UPDATED. */
        @Provides fun initialRules(): AppRules = AppRules(
            fields = listOf(
                FieldRule("name", true),
                FieldRule("email", true),
                FieldRule("restaurant_name", true),
                FieldRule("menu_item", true),
                FieldRule("quantity", true),
                FieldRule("fulfillment_method", true),
                FieldRule("table_number", false),
                FieldRule("special_instructions", false),
                FieldRule("contact_phone", false),
            ),
            ui = mutableMapOf<String, String>().apply {
                this["app_title"] = "Restaurant Ordering (Demo)"
                this["button_label"] = "Place order"
                this["button_color"] = "#079455"
                // Enforce AA contrast: prefer black text on the green button.
                this["button_text_color"] = "#000000"
                if (BuildConfig.DEBUG) {
                    this["banner_message"] = "Demo login: test / test"
                    this["banner_color"] = "#212121"
                    this["banner_text_color"] = "#FFFFFF"
                }
                this["background_color"] = "#FFFFFF"
                this["after_save"] = "success_screen"
                this["success_title"] = "Order placed"
                this["success_message"] = "Your order has been submitted successfully (demo flow)."
            }
        )
    }
}

/** Use cases are pure Kotlin (no @Inject in :domain), so they are provided here. */
@Module
@InstallIn(SingletonComponent::class)
object DomainModule {
    @Provides fun validateProfile() = ValidateProfileUseCase()
    @Provides fun resolveNavigation() = ResolveNavigationUseCase()
    @Provides fun loadProfile(repository: ProfileRepository) = LoadProfileUseCase(repository)
    @Provides fun saveProfile(repository: ProfileRepository) = SaveProfileUseCase(repository)
    @Provides fun observeLiveUpdates(repository: LiveUpdatesRepository) = ObserveLiveUpdatesUseCase(repository)
    @Provides fun authenticateUser() = AuthenticateUserUseCase()
}
