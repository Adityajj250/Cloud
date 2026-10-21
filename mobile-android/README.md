# R.I.F.T. Android app

This Android WebView shell opens the hosted R.I.F.T. mobile interface. The
hosted server URL is preconfigured; use **Change server URL** only when testing
against a different HTTPS endpoint. The PWA remains available in a regular
mobile browser as well.

## Build a debug APK on Windows

From the repository root, run:

```powershell
cd .\mobile-android
.\gradlew.bat assembleDebug
cd ..
New-Item -ItemType Directory -Force .\apk | Out-Null
Copy-Item .\mobile-android\app\build\outputs\apk\debug\app-debug.apk .\apk\RIFT-Mobile-debug.apk -Force
```

The APK is written to `apk/RIFT-Mobile-debug.apk`.

## Install on a connected Android phone

Enable USB debugging, connect the phone, then run from the repository root:

```powershell
adb install -r .\apk\RIFT-Mobile-debug.apk
```

You can also copy the APK to the phone and open it there. Android may ask you
to allow installation from the file app you used. This is a debug build and is
not signed for Play Store distribution.
