package com.example.replyassistant.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * App settings. The API key is stored encrypted with an AES key that lives in the Android
 * Keystore (never leaves secure hardware). androidx EncryptedSharedPreferences is
 * deprecated, so this does the same thing directly.
 */
class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var backendUrl: String
        get() = prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL
        set(value) = prefs.edit().putString(KEY_URL, value.trim()).apply()

    var chatTitle: String
        get() = prefs.getString(KEY_TITLE, "") ?: ""
        set(value) = prefs.edit().putString(KEY_TITLE, value.trim()).apply()

    /** Local pause: no bubble and no reading while true. */
    var paused: Boolean
        get() = prefs.getBoolean(KEY_PAUSED, false)
        set(value) = prefs.edit().putBoolean(KEY_PAUSED, value).apply()

    var tone: String
        get() = prefs.getString(KEY_TONE, "") ?: ""
        set(value) = prefs.edit().putString(KEY_TONE, value).apply()

    var apiKey: String
        get() = prefs.getString(KEY_API, null)?.let { SecretBox.decrypt(it) } ?: ""
        set(value) = prefs.edit().putString(KEY_API, SecretBox.encrypt(value.trim())).apply()

    /** Reminder ids already shown as notifications, so each appears once. */
    var shownReminderIds: Set<String>
        get() = prefs.getStringSet(KEY_SHOWN, emptySet()) ?: emptySet()
        set(value) = prefs.edit().putStringSet(KEY_SHOWN, value).apply()

    val isConfigured: Boolean
        get() = backendUrl.isNotBlank() && apiKey.isNotBlank() && chatTitle.isNotBlank()

    companion object {
        /** USB development: `adb reverse tcp:8000 tcp:8000` maps this to the PC. */
        const val DEFAULT_URL = "http://127.0.0.1:8000"
        private const val KEY_URL = "backend_url"
        private const val KEY_TITLE = "chat_title"
        private const val KEY_PAUSED = "paused"
        private const val KEY_TONE = "tone"
        private const val KEY_API = "api_key_enc"
        private const val KEY_SHOWN = "shown_reminders"
    }
}

private object SecretBox {
    private const val ALIAS = "reply_assistant_api_key"
    private const val TRANSFORM = "AES/GCM/NoPadding"

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return generator.generateKey()
    }

    fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val sealed = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(sealed, Base64.NO_WRAP)
    }

    fun decrypt(encoded: String): String? = try {
        val sealed = Base64.decode(encoded, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, sealed, 0, 12))
        String(cipher.doFinal(sealed, 12, sealed.size - 12), Charsets.UTF_8)
    } catch (e: Exception) {
        null // key was reset (e.g. app data cleared): ask for the API key again
    }
}
