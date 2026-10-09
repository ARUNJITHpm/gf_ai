// Kotlin 2.4.21 is pinned through the Compose and serialization plugins (AGP 9 compiles
// Kotlin itself; the Compose compiler must match the Kotlin compiler exactly).
plugins {
    id("com.android.application") version "9.3.3" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.4.21" apply false
    id("com.google.devtools.ksp") version "2.3.12" apply false
}
