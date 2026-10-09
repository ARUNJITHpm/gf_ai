// Throwaway Phase 0 spike. AGP 9 compiles Kotlin itself, so no Kotlin plugin is applied.
plugins {
    id("com.android.application")
}

android {
    namespace = "com.example.a11yspike"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.example.a11yspike"
        minSdk = 29
        targetSdk = 37
        versionCode = 1
        versionName = "0.1-spike"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.19.0")
}
