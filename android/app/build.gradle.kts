plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
    id("com.google.dagger.hilt.android")
}

// The demo workspace (./start.command --demo) builds a separate "MobileHeal (Demo)" app so it never overwrites
// the app built from your project. Every build also tells the server which folder it was built from.
val demoBuild = (project.findProperty("mobileheal.demo") as String?) == "true"
val sourceFolder = rootProject.projectDir.parentFile.absolutePath.replace("\\", "/")

android {
    namespace = "com.mobileheal.app"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.mobileheal.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
        // Emulator reaches the host's localhost via 10.0.2.2
        buildConfigField("String", "BASE_URL", "\"http://10.0.2.2:8000\"")
        buildConfigField("int", "PROFILE_ID", "1")
        buildConfigField("String", "SOURCE_FOLDER", "\"${sourceFolder.replace("\"", "")}\"")
        buildConfigField("boolean", "DEMO_BUILD", demoBuild.toString())
        manifestPlaceholders["appLabel"] = if (demoBuild) "MobileHeal (Demo)" else "MobileHeal"
        if (demoBuild) applicationIdSuffix = ".demo"
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
    implementation(project(":domain"))
    implementation(project(":data"))

    val composeBom = platform("androidx.compose:compose-bom:2024.09.00")
    implementation(composeBom)
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.5")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.5")
    implementation("androidx.navigation:navigation-compose:2.8.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    debugImplementation("androidx.compose.ui:ui-tooling")

    implementation("com.google.dagger:hilt-android:2.52")
    ksp("com.google.dagger:hilt-compiler:2.52")
    implementation("androidx.hilt:hilt-navigation-compose:1.2.0")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
}
