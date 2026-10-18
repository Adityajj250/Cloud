# R.I.F.T. Mobile Access Guide

Use the browser PWA or native Android app to access the command center from a phone on any internet connection. The worker PC keeps an outbound connection to AWS IoT Core, and a Cloudflare Quick Tunnel gives the phone a temporary public HTTPS route to the local server. No router port forwarding or shared Wi-Fi is needed.

## Start the public test URL

1. Install `cloudflared` using the [official Windows instructions](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
2. From the project folder, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\start_public_test.ps1
   ```

3. Open the `https://....trycloudflare.com` URL printed by the script in Chrome on the phone. On the laptop, open the same URL with `?view=laptop` appended (for example, `https://example.trycloudflare.com/?view=laptop`).
4. Keep the PowerShell window open. Press Ctrl+C to stop the tunnel and server. A restarted tunnel gets a new URL.

The worker PC must remain powered on and connected to the internet. In a fresh Python environment, install the agent dependencies once with `python -m pip install -r .\agent\requirements.txt`, then start it in a separate PowerShell window with `python .\agent\rift_agent.py`. The agent terminal prints a six-digit Screen Share pairing PIN. In the phone's **Screen Share** tab, enter that PIN to view and control the laptop. The preview is sent at up to 1280×720 and about 5 frames per second, with the cursor drawn into every frame. **Full screen** requests landscape orientation and opens a translucent virtual keyboard over the shared screen; **Touchpad** opens the mouse controls. Only one phone can control it at a time; tap **Stop** on the phone to end the session.

## Native Android app

In Android Studio, open the `mobile-android` folder, let Gradle sync, then run the `app` configuration on a phone. You can also build the debug APK from PowerShell with `.gradlew.bat :app:assembleDebug`; it is written to `app\build\outputs\apk\debug\app-debug.apk`. On first launch, paste the HTTPS URL printed by the tunnel script. The app saves it; use **Set tunnel URL** in the top bar after restarting the Quick Tunnel. Start the phone camera in the Vision tab and grant Android camera permission when prompted.

## Testing limitations

- This temporary tunnel is for development. Anyone with the URL can reach the server, so avoid sensitive data and stop the tunnel when testing is done.
- Cloudflare Quick Tunnels do not support Server-Sent Events. The LLM endpoint now returns its completed response instead of streaming tokens.
- The laptop console shows unique connected phone browsers, can ping each phone, previews the active phone camera, and shows completed compute results. Phones can ping the laptop page or the worker agent.
- Remote screen control uses the Windows agent's interactive desktop. It cannot interact with the Windows lock screen or secure desktop. Keep the pairing PIN private; anyone with the public URL and the current PIN can control the desktop until the session is stopped or the agent exits.
