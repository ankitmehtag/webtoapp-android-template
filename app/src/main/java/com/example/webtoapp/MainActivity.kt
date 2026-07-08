package com.example.webtoapp

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Scaffold
import androidx.compose.ui.Modifier
import com.example.webtoapp.ui.theme.WebToAppTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // ── Deep Linking: read URL from intent data if launched via a link ──
        val deepLinkUrl = intent?.data?.toString()
        val startUrl = if (!deepLinkUrl.isNullOrEmpty()) deepLinkUrl else "https://www.google.com"

        setContent {
            WebToAppTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { _ ->
                    WebPage(url = startUrl)
                }
            }
        }
    }
}
