package com.rift.commandcenter;

import android.Manifest;
import android.app.DownloadManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Environment;
import android.os.Bundle;
import android.text.InputType;
import android.view.ViewGroup;
import android.view.View;
import android.content.pm.ActivityInfo;
import android.webkit.DownloadListener;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.ValueCallback;
import android.webkit.URLUtil;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Toast;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

/** Native shell for the R.I.F.T. web command center. */
public class MainActivity extends AppCompatActivity {
    private static final String PREFS = "rift_mobile";
    private static final String SERVER_URL_KEY = "server_url";
    private static final String DEFAULT_SERVER_URL = "https://d2nv2vasdntzxl.cloudfront.net";
    private static final int CAMERA_PERMISSION_REQUEST = 41;
    private static final int FILE_CHOOSER_REQUEST = 42;

    private WebView webView;
    private LinearLayout rootLayout;
    private PermissionRequest pendingCameraRequest;
    private ValueCallback<Uri[]> pendingFileChooser;
    private View fullscreenVideoView;
    private WebChromeClient.CustomViewCallback fullscreenVideoCallback;
    private String serverUrl;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        serverUrl = getSharedPreferences(PREFS, MODE_PRIVATE).getString(SERVER_URL_KEY, DEFAULT_SERVER_URL);
        createLayout();
        configureWebView();

        if (serverUrl.isEmpty()) showServerUrlDialog(DEFAULT_SERVER_URL);
        else loadCommandCenter();
    }

    private void createLayout() {
        rootLayout = new LinearLayout(this);
        rootLayout.setOrientation(LinearLayout.VERTICAL);

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setOrientation(LinearLayout.HORIZONTAL);
        Button changeUrl = new Button(this);
        changeUrl.setText("Change server URL");
        changeUrl.setOnClickListener(v -> showServerUrlDialog(serverUrl));
        Button reload = new Button(this);
        reload.setText("Reload");
        reload.setOnClickListener(v -> loadCommandCenter());
        toolbar.addView(changeUrl, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        toolbar.addView(reload, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        rootLayout.addView(toolbar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        webView = new WebView(this);
        rootLayout.addView(webView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(rootLayout);
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                Uri target = Uri.parse(url);
                Uri origin = Uri.parse(serverUrl);
                // Keep navigation within the configured HTTPS tunnel.
                return !"https".equalsIgnoreCase(target.getScheme())
                        || target.getHost() == null
                        || !target.getHost().equalsIgnoreCase(origin.getHost());
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingFileChooser != null) pendingFileChooser.onReceiveValue(null);
                pendingFileChooser = callback;
                try {
                    Intent intent = params.createIntent();
                    startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                    return true;
                } catch (Exception error) {
                    pendingFileChooser = null;
                    callback.onReceiveValue(null);
                    Toast.makeText(MainActivity.this, "Could not open the Android file picker.", Toast.LENGTH_LONG).show();
                    return false;
                }
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> handleWebPermissionRequest(request));
            }

            @Override
            public void onPermissionRequestCanceled(PermissionRequest request) {
                if (pendingCameraRequest == request) pendingCameraRequest = null;
            }

            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (fullscreenVideoView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                fullscreenVideoView = view;
                fullscreenVideoCallback = callback;
                if (rootLayout.getChildCount() > 0) rootLayout.getChildAt(0).setVisibility(View.GONE);
                rootLayout.removeView(webView);
                rootLayout.addView(view, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
            }

            @Override
            public void onHideCustomView() {
                hideFullscreenVideo();
            }
        });

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            Uri downloadUri = Uri.parse(url);
            Uri origin = Uri.parse(serverUrl);
            if (!"https".equalsIgnoreCase(downloadUri.getScheme()) || downloadUri.getHost() == null
                    || !downloadUri.getHost().equalsIgnoreCase(origin.getHost())) {
                Toast.makeText(this, "Blocked a download from outside the configured R.I.F.T. server.", Toast.LENGTH_LONG).show();
                return;
            }
            try {
                String filename = URLUtil.guessFileName(url, contentDisposition, mimeType)
                        .replaceAll("[^A-Za-z0-9._-]", "_");
                if (filename.isEmpty()) filename = "RIFT-" + System.currentTimeMillis();
                DownloadManager.Request request = new DownloadManager.Request(downloadUri)
                        .setTitle(filename)
                        .setDescription("Downloading from R.I.F.T. to Downloads")
                        .setMimeType(mimeType == null ? "application/octet-stream" : mimeType)
                        .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                        .setAllowedOverMetered(true)
                        .setAllowedOverRoaming(false)
                        .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename);
                DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
                manager.enqueue(request);
                Toast.makeText(this, "Download started. Check Android Downloads.", Toast.LENGTH_LONG).show();
            } catch (Exception error) {
                Toast.makeText(this, "Could not start download: " + error.getMessage(), Toast.LENGTH_LONG).show();
            }
        });
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_CHOOSER_REQUEST && pendingFileChooser != null) {
            Uri[] results = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
            pendingFileChooser.onReceiveValue(results);
            pendingFileChooser = null;
        }
    }

    private void hideFullscreenVideo() {
        if (fullscreenVideoView == null) return;
        rootLayout.removeView(fullscreenVideoView);
        if (rootLayout.getChildCount() > 0) rootLayout.getChildAt(0).setVisibility(View.VISIBLE);
        rootLayout.addView(webView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        fullscreenVideoView = null;
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
        if (fullscreenVideoCallback != null) fullscreenVideoCallback.onCustomViewHidden();
        fullscreenVideoCallback = null;
    }

    private void showServerUrlDialog(String initialValue) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint(DEFAULT_SERVER_URL);
        input.setText(initialValue);
        input.setSelection(input.getText().length());
        int pad = (int) (20 * getResources().getDisplayMetrics().density);

        new AlertDialog.Builder(this)
                .setTitle("Connect to R.I.F.T.")
                .setMessage("The hosted R.I.F.T. server URL is filled in by default. Change it only if you are connecting to a local development tunnel.")
                .setView(input, pad, 0, pad, 0)
                .setCancelable(!serverUrl.isEmpty())
                .setPositiveButton("Connect", (dialog, which) -> saveAndLoad(input.getText().toString()))
                .setNegativeButton(serverUrl.isEmpty() ? "Cancel" : "Keep current", (dialog, which) -> {
                    if (serverUrl.isEmpty()) finish();
                })
                .show();
    }

    private void saveAndLoad(String value) {
        Uri uri = Uri.parse(value.trim());
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null) {
            Toast.makeText(this, "Enter a valid HTTPS tunnel URL.", Toast.LENGTH_LONG).show();
            showServerUrlDialog(value.trim());
            return;
        }
        serverUrl = uri.buildUpon().clearQuery().fragment(null).build().toString();
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(SERVER_URL_KEY, serverUrl).apply();
        loadCommandCenter();
    }

    private void loadCommandCenter() {
        if (serverUrl == null || serverUrl.isEmpty()) return;
        Uri mobileUri = Uri.parse(serverUrl).buildUpon().appendQueryParameter("view", "mobile").build();
        webView.loadUrl(mobileUri.toString());
    }

    private void handleWebPermissionRequest(PermissionRequest request) {
        Uri requestedOrigin = Uri.parse(request.getOrigin().toString());
        Uri configuredOrigin = Uri.parse(serverUrl);
        if (!"https".equalsIgnoreCase(requestedOrigin.getScheme())
                || requestedOrigin.getHost() == null
                || !requestedOrigin.getHost().equalsIgnoreCase(configuredOrigin.getHost())) {
            request.deny();
            return;
        }

        boolean requestsCamera = false;
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) requestsCamera = true;
        }
        if (!requestsCamera) {
            request.deny();
            return;
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
        } else {
            pendingCameraRequest = request;
            requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_PERMISSION_REQUEST);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != CAMERA_PERMISSION_REQUEST || pendingCameraRequest == null) return;
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            pendingCameraRequest.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
        } else {
            pendingCameraRequest.deny();
            Toast.makeText(this, "Camera permission is needed for the Vision tab.", Toast.LENGTH_LONG).show();
        }
        pendingCameraRequest = null;
    }

    @Override
    public void onBackPressed() {
        if (fullscreenVideoView != null) hideFullscreenVideo();
        else if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }
}
