package com.example.webtoapp

import android.Manifest
import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Environment
import android.webkit.*
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat

@SuppressLint("SetJavaScriptEnabled")
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WebPage(url: String) {
    val context = LocalContext.current

    var webView       by remember { mutableStateOf<WebView?>(null) }
    var progress      by remember { mutableFloatStateOf(0f) }
    var isLoading     by remember { mutableStateOf(true) }
    var isRefreshing  by remember { mutableStateOf(false) }
    var isOffline     by remember { mutableStateOf(false) }
    var showExitDialog by remember { mutableStateOf(false) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {}

    if (showExitDialog) {
        AlertDialog(
            onDismissRequest = { showExitDialog = false },
            title   = { Text(stringResource(R.string.exit_title)) },
            text    = { Text(stringResource(R.string.exit_message)) },
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

    BackHandler {
        when {
            isOffline                    -> { isOffline = false; webView?.reload() }
            webView?.canGoBack() == true -> webView?.goBack()
            else                         -> showExitDialog = true
        }
    }

    Box(Modifier.fillMaxSize()) {
        if (isOffline) {
            OfflineScreen(onRetry = { isOffline = false; webView?.reload() })
        } else {
            val content: @Composable () -> Unit = {
                Box(Modifier.fillMaxSize()) {
                    WebViewContainer(
                        url         = url,
                        onCreated   = { webView = it },
                        onProgress  = { p ->
                            progress     = p / 100f
                            isLoading    = p < 100
                            if (p == 100) isRefreshing = false
                        },
                        onOffline   = { isOffline = true },
                        onPermission = { req ->
                            val needed = req.resources.mapNotNull { r ->
                                when (r) {
                                    PermissionRequest.RESOURCE_VIDEO_CAPTURE -> Manifest.permission.CAMERA
                                    PermissionRequest.RESOURCE_AUDIO_CAPTURE -> Manifest.permission.RECORD_AUDIO
                                    else -> null
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
                                ) == PackageManager.PERMISSION_GRANTED
                            ) cb.invoke(origin, true, false)
                            else permissionLauncher.launch(
                                arrayOf(
                                    Manifest.permission.ACCESS_FINE_LOCATION,
                                    Manifest.permission.ACCESS_COARSE_LOCATION,
                                )
                            )
                        },
                        onDownload = { dlUrl, ua, cd, mime ->
                            val fileName = URLUtil.guessFileName(dlUrl, cd, mime)
                            val req = DownloadManager.Request(Uri.parse(dlUrl)).apply {
                                setMimeType(mime)
                                addRequestHeader("User-Agent", ua)
                                setTitle(fileName)
                                setDescription(context.getString(R.string.download_in_progress))
                                setNotificationVisibility(
                                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED
                                )
                                setDestinationInExternalPublicDir(
                                    Environment.DIRECTORY_DOWNLOADS, fileName
                                )
                            }
                            (context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager)
                                .enqueue(req)
                            Toast.makeText(context, R.string.download_started, Toast.LENGTH_SHORT)
                                .show()
                        },
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
                PullToRefreshBox(
                    isRefreshing = isRefreshing,
                    onRefresh    = { isRefreshing = true; webView?.reload() },
                ) { content() }
            } else {
                content()
            }
        }
    }
}

@Composable
private fun OfflineScreen(onRetry: () -> Unit) {
    Column(
        modifier            = Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("📡", style = MaterialTheme.typography.displayLarge)
        Spacer(Modifier.height(16.dp))
        Text(
            text      = stringResource(R.string.offline_title),
            style     = MaterialTheme.typography.titleLarge,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text      = stringResource(R.string.offline_message),
            style     = MaterialTheme.typography.bodyMedium,
            color     = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(24.dp))
        Button(
            onClick = onRetry,
            colors  = ButtonDefaults.buttonColors(containerColor = Config.primaryColor),
        ) {
            Text(stringResource(R.string.offline_retry))
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun WebViewContainer(
    url            : String,
    onCreated      : (WebView) -> Unit,
    onProgress     : (Int) -> Unit,
    onOffline      : () -> Unit,
    onPermission   : (PermissionRequest) -> Unit,
    onGeoPermission: (String, GeolocationPermissions.Callback) -> Unit,
    onDownload     : (url: String, ua: String, cd: String, mime: String) -> Unit,
) {
    val latestOnOffline      = rememberUpdatedState(onOffline)
    val latestOnPermission   = rememberUpdatedState(onPermission)
    val latestOnGeoPermission = rememberUpdatedState(onGeoPermission)
    val latestOnDownload     = rememberUpdatedState(onDownload)

    AndroidView(
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

                CookieManager.getInstance().setAcceptCookie(true)

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
                        error   : WebResourceError,
                    ) {
                        if (request.isForMainFrame) latestOnOffline.value()
                    }

                    override fun shouldOverrideUrlLoading(
                        view    : WebView,
                        request : WebResourceRequest,
                    ): Boolean {
                        val uri = request.url
                        return when (uri.scheme?.lowercase()) {
                            "http", "https" -> false
                            "tel"     -> { ctx.startActivity(Intent(Intent.ACTION_DIAL, uri)); true }
                            "mailto"  -> { ctx.startActivity(Intent(Intent.ACTION_SENDTO, uri)); true }
                            else      -> {
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
                        latestOnPermission.value(request)

                    override fun onGeolocationPermissionsShowPrompt(
                        origin   : String,
                        callback : GeolocationPermissions.Callback,
                    ) = latestOnGeoPermission.value(origin, callback)
                }

                setDownloadListener { dlUrl, ua, cd, mime, _ ->
                    latestOnDownload.value(dlUrl, ua, cd, mime)
                }

                onCreated(this)
                loadUrl(url)
            }
        }
    )
}
