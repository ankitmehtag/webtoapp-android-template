package com.example.webtoapp

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Scaffold
import androidx.compose.ui.Modifier
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import com.example.webtoapp.ui.theme.WebToAppTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        // Must be called before super.onCreate() so the SplashScreen API
        // can install itself and transition from Theme.WebToApp.Starting.
        installSplashScreen()

        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // Deep link URL wins; fall back to Config.URL
        val startUrl = intent?.data?.toString()?.takeIf { it.isNotEmpty() } ?: Config.URL

        setContent {
            WebToAppTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { _ ->
                    WebPage(url = startUrl)
                }
            }
        }
    }
}
