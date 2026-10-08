package com.mobileheal.data.di

import com.mobileheal.data.repository.InMemoryRulesRepository
import com.mobileheal.data.repository.LiveUpdatesRepositoryImpl
import com.mobileheal.data.repository.ProfileRepositoryImpl
import com.mobileheal.domain.repository.LiveUpdatesRepository
import com.mobileheal.domain.repository.ProfileRepository
import com.mobileheal.domain.repository.RulesRepository
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class DataModule {
    @Binds @Singleton abstract fun profileRepository(impl: ProfileRepositoryImpl): ProfileRepository
    @Binds @Singleton abstract fun liveUpdatesRepository(impl: LiveUpdatesRepositoryImpl): LiveUpdatesRepository
    @Binds abstract fun rulesRepository(impl: InMemoryRulesRepository): RulesRepository

    companion object {
        @Provides
        @Singleton
        fun okHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }
}
