plugins {
    id("com.android.application") version "8.6.0" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.0.21" apply false
    // Kotlin 2.0+ moved the Compose compiler out of the Kotlin compiler
    // itself into this separate plugin - required whenever Compose is
    // enabled (`buildFeatures.compose = true` in app/build.gradle.kts),
    // or the build fails at configuration time with "Starting in Kotlin
    // 2.0, the Compose Compiler Gradle plugin is required".
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
    id("com.google.devtools.ksp") version "2.0.21-1.0.28" apply false
}
