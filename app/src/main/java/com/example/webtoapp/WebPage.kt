package com.example.webtoapp

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.webkit.*
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.google.accompanist.swiperefresh.SwipeRefresh
import com.google.accompanist.swiperefresh.rememberSwipeRefreshState

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun WebPage(url: String) {
    val context = LocalContext.current

    var webView: WebView? by remember { mutableStateOf(null) }
    var progress by remember { mutableFloatStateOf(0f) }
    var isLoading by remember { mutableStateOf(true) }
    var isRefreshing by remember { mutableStateOf(false) }
    var showExitDialog by remember { mutableStateOf(false) }
    var isOffline by remember { mutableStateOf(false) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {}

    // ── Exit confirmation ────────────────────────────────────────────────
    if (showExitDialog) {
        AlertDialog(
            onDismissRequest = { showExitDialog = false },
            title = { Text(stringResource(R.string.exit_title)) },
            text  = { Text(stringResource(R.string.exit_message)) },
            confirmButton = {
                TextButton(onClick = {
                    showExitDialog = false
                    (context as? androidx.activity.ComponentActivity)?.finish()
                }) { Text(stringResource(R.string.exit_confirm)) }
            },
            dismissButton = {
                TextButton(onClick = { showExitDialog = false }) {
                    Text(stringResource(R.string.exit_cancel))
                }
            }
        )
    }

    // ── Back navigation ──────────────────────────────────────────────────
    BackHandler {
        when {
            isOffline                    -> { isOffline = false; webView?.reload() }
            webView?.canGoBack() == true -> webView?.goBack()
            else                         -> showExitDialog = true
        }
    }

    val swipeState = rememberSwipeRefreshState(isRefreshing)

    Box(Modifier.fillMaxSize()) {
        if (isOffline) {
            OfflineScreen(onRetry = { isOffline = false; webView?.reload() })
        } else {
            val content: @Composable () -> Unit = {
                Box(Modifier.fillMaxSize()) {
                    WebViewContainer(
                        url             = url,
                        onCreated       = { wv -> webView = wv },
                        onProgress      = { p ->
                            progress    = p / 100f
                            isLoading   = p < 100
                            if (p == 100) isRefreshing = false
                        },
                        onOffline       = { isOffline = true },
                        onPermission    = { req ->
                            val needed = buildList {
                                req.resources.forEach { r ->
                                    when (r) {
                                        PermissionRequest.RESOURCE_VIDEO_CAPTURE -> add(Manifest.permission.CAMERA)
                                        PermissionRequest.RESOURCE_AUDIO_CAPTURE -> add(Manifest.permission.RECORD_AUDIO)
                                    }
                                }
                            }
                            if (needed.all {
                                ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
                            }) req.grant(req.resources)
                            else permissionLauncher.launch(needed.toTypedArray())
                        },
                        onGeoPermission = { origin, cb ->
                            if (ContextCompat.checkSelfPermission(
                                context, Manifest.permission.ACCESS_FINE_LOCATION
                            ) == PackageManager.PERMISSION_GRANTED) {
                                cb.invoke(origin, true, false)
                            } else {
                                permissionLauncher.launch(arrayOf(
                                    Manifest.permission.ACCESS_FINE_LOCATION,
                                    Manifest.permission.ACCESS_COARSE_LOCATION
                                ))
                            }
                        }
                    )

                    if (isLoading) {
                        LinearProgressIndicator(
                            progress   = { progress },
                            modifier   = Modifier.fillMaxWidth().align(Alignment.TopCenter),
                            color      = Config.primaryColor,
                            trackColor = Color.Transparent,
                        )
                    }
                }
            }

            if (Config.PULL_TO_REFRESH) {
                SwipeRefresh(
                    state     = swipeState,
                    onRefresh = { isRefreshing = true; webView?.reload() },
                    content   = content
                )
            } else {
                content()
            }
        }
    }
}

// ── Offline screen ───────────────────────────────────────────────────────────

@Composable
private fun OfflineScreen(onRetry: () -> Unit) {
    Column(
        modifier            = Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text("📡", style = MaterialTheme.typography.displayLarge)
        Spacer(Modifier.height(16.dp))
        Text(
            text      = stringResource(R.string.offline_title),
            style     = MaterialTheme.typography.titleLarge,
            textAlign = TextAlign.Center
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text      = stringResource(R.string.offline_message),
            style     = MaterialTheme.typography.bodyMedium,
            color     = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center
        )
        Spacer(Modifier.height(24.dp))
        Button(
            onClick = onRetry,
            colors  = ButtonDefaults.buttonColors(containerColor = Config.primaryColor)
        ) {
            Text(stringResource(R.string.offline_retry))
        }
    }
}

// ── WebView container ────────────────────────────────────────────────────────

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun WebViewContainer(
    url            : String,
    onCreated      : (WebView) -> Unit,
    onProgress     : (Int) -> Unit,
    onOffline      : () -> Unit,
    onPermission   : (PermissionRequest) -> Unit,
    onGeoPermission: (String, GeolocationPermissions.Callback) -> Unit,
) {
    val context = LocalContext.current

    androidx.compose.ui.viewinterop.AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory  = { ctx ->
            WebView(ctx).apply {
                settings.apply {
                    javaScriptEnabled                = true
                    domStorageEnabled                = true
                    databaseEnabled                  = true
                    loadWithOverviewMode             = true
                    useWideViewPort                  = true
                    setSupportZoom(true)
                    builtInZoomControls              = true
                    displayZoomControls              = false
                    mediaPlaybackRequiresUserGesture = false
                    setGeolocationEnabled(true)
                    mixedContentMode                 = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
                }

                CookieManager.getInstance().apply {
                    setAcceptCookie(true)
                    setAcceptThirdPartyCookies(this@apply, true)
                }

                webViewClient = object : WebViewClient() {

                    override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) =
                        onProgress(0)

                    override fun onPageFinished(view: WebView, url: String) {
                        onProgress(100)
                        CookieManager.getInstance().flush()
                    }

                    override fun onReceivedError(
                        view    : WebView,
                        request : WebResourceRequest,
                        error   : WebResourceError
                    ) {
                        if (!request.isForMainFrame) return
                        val networkErrors = setOf(
                            WebViewClient.ERROR_HOST_LOOKUP,
                            WebViewClient.ERROR_CONNECT,
                            WebViewClient.ERROR_INTERNET_DISCONNECTED,
                            WebViewClient.ERROR_TIMEOUT,
                            WebViewClient.ERROR_UNKNOWN
                        )
                        if (error.errorCode in networkErrors) onOffline()
                    }

                    override fun shouldOverrideUrlLoading(
                        view    : WebView,
                        request : WebResourceRequest
                    ): Boolean {
                        val uri = request.url
                        return when (uri.scheme?.lowercase()) {
                            "http", "https" -> false   // load inside WebView

                            "tel" -> {
                                ctx.startActivity(Intent(Intent.ACTION_DIAL, uri))
                                true
                            }
                            "mailto" -> {
                                ctx.startActivity(Intent(Intent.ACTION_SENDTO, uri))
                                true
                            }
                            "whatsapp" -> {
                                runCatching {
                                    ctx.startActivity(
                                        Intent(Intent.ACTION_VIEW, uri).setPackage("com.whatsapp")
                                    )
                                }
                                true
                            }
                            "intent" -> {
                                runCatching {
                                    val intent = Intent.parseUri(
                                        uri.toString(), Intent.URI_INTENT_SCHEME
                                    )
                                    if (intent.resolveActivity(ctx.packageManager) != null) {
                                        ctx.startActivity(intent)
                                    } else {
                                        intent.getStringExtra("browser_fallback_url")
                                            ?.let { view.loadUrl(it) }
                                    }
                                }
                                true
                            }
                            else -> {
                                runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, uri)) }
                                true
                            }
                        }
                    }
                }

                webChromeClient = object : WebChromeClient() {
                    override fun onProgressChanged(view: WebView, newProgress: Int) =
                        onProgress(newProgress)

                    override fun onPermissionRequest(request: PermissionRequest) =
                        onPermission(request)

                    override fun onGeolocationPermissionsShowPrompt(
                        origin   : String,
                        callback : GeolocationPermissions.Callback
                    ) = onGeoPermission(origin, callback)
                }

                onCreated(this)
                loadUrl(url)
            }
        }
    )
}
