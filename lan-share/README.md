# LAN Share — Step 1

A laptop hosts a small HTTP server. A phone on the same local network opens its page to upload and download files. No cloud, account, router port forwarding, or phone app is required.

## Run on Windows

1. Install Python 3.10 or newer from https://www.python.org/downloads/ if needed. Verify in PowerShell with `py --version`.
2. Extract this ZIP first. Open the extracted `lan-share` folder. In File Explorer's address bar, type `powershell` and press Enter.
3. Run `py server.py`. Leave this window open. The server creates a `shared` folder and prints a random connection key. The key changes each time you restart it.
4. On your laptop, open http://localhost:8000 and enter the printed key. Click **Connect / refresh**.
5. Connect your phone and laptop to the same trusted home Wi-Fi. In a second PowerShell window run `ipconfig`. Find **IPv4 Address** under the active **Wireless LAN adapter Wi-Fi** (not a VPN or virtual adapter). Example: `192.168.1.20`.
6. On your phone, open `http://192.168.1.20:8000`, replacing the example with your laptop's actual IPv4 address. Use `http`, not `https`. Enter the same connection key and tap **Connect / refresh**.
7. Choose a small file on the phone and tap **Upload files**. It appears in the laptop's `shared` folder.
8. Copy a laptop file into `shared`, refresh the phone page, then tap **Download**. Your browser may save it to Downloads or show a save/share prompt.
9. Press Ctrl+C in the server window to stop sharing. Files remain on disk.

If Windows asks about Python network access, allow it on your trusted **Private** network. Do not disable the firewall. If needed, check Windows Security → Firewall & network protection → Allow an app through firewall and permit the Python executable on Private networks.

On macOS/Linux, use `python3 server.py` in a terminal instead; find the Wi-Fi IPv4 address in network settings. The same browser steps apply.

## How this was built

1. **Server:** `server.py` uses Python's built-in `ThreadingHTTPServer` to listen on port 8000. Binding to `0.0.0.0` lets other devices reach it through the laptop's network interfaces; it is not the address you enter on your phone.
2. **Page:** `index.html` supplies a responsive interface using HTML, CSS, and JavaScript. The laptop sends this page when the browser requests `/`.
3. **Connection:** Each launch generates a random key. JavaScript includes that key in an `X-Share-Key` header for file API requests. The page is public; file listing, uploads, and downloads require the key.
4. **Listing:** `GET /api/files` returns filenames and sizes from `shared`. The page builds its file list from this JSON response.
5. **Upload:** `POST /api/upload?name=...` sends a file's raw bytes. The server writes chunks to a temporary file, then moves the completed upload into `shared`. Duplicate filenames are rejected. Multiple selected files upload sequentially.
6. **Download:** `GET /api/download?name=...` sends file bytes back. JavaScript creates a browser download. Downloads are buffered in the phone's memory, so this prototype limits both uploads and downloads to 100 MiB per file.
7. **Boundaries:** Filename checks reject paths and symbolic links. Only files immediately inside `shared` are listed; subfolder browsing is not included. Upload progress measures bytes sent, and completion appears after the server responds.

Your Wi-Fi router forwards traffic locally between the devices. Internet access is not needed after Python and this project are installed. The laptop must remain awake and the server must keep running.

## Troubleshooting

- **Phone cannot connect:** confirm the laptop's local page works first. Recheck its IPv4 address, firewall, and Wi-Fi. Guest Wi-Fi or client isolation can prevent devices from communicating even on the same Wi-Fi name. VPNs can also interfere.
- **Key rejected:** use the current key from the running server, not a previous launch.
- **Port already in use:** run `py server.py --port 8001` and use port 8001 in both browser URLs.
- **Filename exists:** rename the incoming file before uploading. No file is silently replaced.
- **Large download fails:** try a smaller file; mobile memory is limited.
- **Interrupted upload:** retry. Partial uploads are not published in `shared`; after a forced termination, an old `.uploads-...` temporary folder can be removed when the server is stopped.

## Scope

This is a learning prototype for a trusted LAN. HTTP does not encrypt files or the connection key. Do not expose port 8000 to the internet or use it on public Wi-Fi. The built-in Python server is not a production internet server. Keep only files you intend to share in `shared`. There is no folder browsing, remote desktop, resumable transfer, or different-network access yet.

## Reference documentation

- Python HTTP server: https://docs.python.org/3/library/http.server.html
- Windows ipconfig: https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/ipconfig
