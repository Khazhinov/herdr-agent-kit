# Ovis for Herdr App on a phone

English | [Русский](ovis-mobile.ru.md)

[Ovis](https://github.com/devswha/ovis) opens the existing spaces, tabs and agent terminals of
Herdr App. This setup uses the Linux server from Ovis 0.5.9 with `hc` 0.1.16 in WSL2 mirrored
networking on Windows. The server connects to the same core as Herdr App as a user service. On the
PC Ovis uses loopback HTTP. A phone on the home Wi-Fi uses a separate HTTPS listener that requires
pairing from the first connection.

For access away from home, Ovis offers its own [cloud relay](https://github.com/devswha/ovis/blob/v0.5.9/docs/guide.md#through-the-ovis-relay)
and also supports [Tailscale](https://github.com/devswha/ovis/blob/v0.5.9/docs/guide.md#through-tailscale).
Android allows one active VPN service per user or profile. Starting Tailscale can stop an existing
VPN client such as v2rayNG. Plain LAN HTTPS and the Ovis cloud relay use no Android VPN profile.
Choose LAN HTTPS when access at home is enough and you want to keep the existing VPN connected.
If that VPN routes LAN traffic into the tunnel, configure a direct route to the Ovis address.
[Android VPN documentation](https://developer.android.com/develop/connectivity/vpn)

## Install in the WSL distribution that owns the terminals

Use the Linux user that already sees the Herdr App terminals with `hc ls`. Check `hc --version`,
`systemctl --user status` and `ip -br -4 addr` in that WSL distribution. These instructions use
x86-64, Bash, OpenSSL, `gh`, and a running systemd user manager. Use an IPv4 address on the
interface reached from home Wi-Fi. With mirrored networking, this can be the Windows LAN address.
The stock loopback port is 7317. This example uses 7318 for LAN HTTPS. See the
[WSL networking guide](https://learn.microsoft.com/windows/wsl/networking#mirrored-mode-networking)
if the LAN listener works inside WSL but the phone cannot reach it.

Download the pinned release binaries and verify their SHA-256 digests before extracting. The
standalone Ovis Desktop app starts its own core. For the current Herdr App terminals, run the
Linux server from the [Ovis 0.5.9 release](https://github.com/devswha/ovis/releases/tag/v0.5.9)
with `OVIS_RUNTIME=herdr-core`.

<!-- install-block -->
```bash
(
  set -euo pipefail
  stage=$(mktemp -d)
  trap 'rm -rf -- "$stage"' EXIT
  cd "$stage"
  gh release download v0.5.9 -R devswha/ovis \
    -p ovis-server-linux-x64 -p ovis-dist.tar.gz
  sha256sum -c <<'SUMS'
dc83fe650741a8e266c03bec1db316af86921805152587589ea16f8d74dea4ce  ovis-server-linux-x64
cbbd8a1d3a122c3b92eeabad79d454b73ea39e9dffabf5298bade1081922efef  ovis-dist.tar.gz
SUMS
  ovis_bin="$HOME/.local/share/ovis/bin"
  install -d "$ovis_bin/dist"
  install -m 755 ovis-server-linux-x64 "$ovis_bin/ovis-server"
  tar -xzf ovis-dist.tar.gz --no-same-owner -C "$ovis_bin/dist"
)
```
<!-- /install-block -->

## Configure HTTPS and start the service

Replace `YOUR_WSL_LAN_IPV4` with the chosen LAN address. The commands are for a fresh TLS setup.
Keep the CA private key in WSL. Copy only the public CA certificate to the phone. When the LAN IP
changes, update the listener address and issue a new server certificate with the new IP in its
subject alternative name. Reuse the CA to keep the existing Android trust entry. The example
creates a server certificate valid for one year.

<!-- tls-block -->
```bash
export OVIS_LAN_IP='YOUR_WSL_LAN_IPV4'
(
  set -euo pipefail
  umask 077
  tls="$HOME/.config/ovis/tls"
  install -d -m 700 "$tls"
  cd "$tls"
  test ! -e root.key
  test ! -e root.crt
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -sha256 -days 1825 -subj '/CN=Ovis Home Wi-Fi' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -keyout root.key -out root.crt
  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -subj "/CN=$OVIS_LAN_IP" -keyout server.key -out server.csr
  openssl x509 -req -in server.csr -CA root.crt -CAkey root.key \
    -set_serial "0x$(openssl rand -hex 16)" -days 365 -sha256 -out server.crt \
    -extfile <(printf '%s\n' 'basicConstraints=critical,CA:FALSE' \
      'keyUsage=critical,digitalSignature' 'extendedKeyUsage=serverAuth' \
      "subjectAltName=IP:$OVIS_LAN_IP")
  rm -- server.csr
  openssl verify -x509_strict -CAfile root.crt -verify_ip "$OVIS_LAN_IP" server.crt
  openssl x509 -in root.crt -outform DER -out "$HOME/Ovis-Home-WiFi.crt"
)
```
<!-- /tls-block -->

Save this unit as `~/.config/systemd/user/ovis.service`. Create the directory if needed. Substitute
the LAN IPv4 again. The unit assumes `hc` is in `~/.local/bin`. If it is installed elsewhere, use the absolute path
from `command -v hc`. With a named core or custom socket, set `HC_SESSION`
or `HC_SOCKET` to the value used by the current Herdr App session.

<!-- unit-block -->
```ini
[Unit]
Description=Ovis for existing Herdr App sessions

[Service]
WorkingDirectory=%h/.local/share/ovis/bin
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=OVIS_RUNTIME=herdr-core
Environment=OVIS_HC_BIN=%h/.local/bin/hc
Environment=OVIS_TELEMETRY=0
Environment=OVIS_ACCOUNT_SERVICE_URL=off
Environment=HOST=127.0.0.1
Environment=PORT=7317
Environment=OVIS_LAN_HTTPS_PORT=7318
Environment=OVIS_LAN_HTTPS_HOST=YOUR_WSL_LAN_IPV4
Environment=OVIS_LAN_HTTPS_CERT=%h/.config/ovis/tls/server.crt
Environment=OVIS_LAN_HTTPS_KEY=%h/.config/ovis/tls/server.key
ExecStart=%h/.local/share/ovis/bin/ovis-server serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now ovis.service
journalctl --user -u ovis.service -n 30 --no-pager
curl --noproxy '*' -fsS http://127.0.0.1:7317/api/health
curl --noproxy '*' -sS --cacert "$HOME/.config/ovis/tls/root.crt" \
  -o /dev/null -w '%{http_code}\n' "https://$OVIS_LAN_IP:7318/api/session"
```
<!-- /unit-block -->

The health response should show `runtime: herdr-core`, terminal attachment enabled and the relay
phase `off`. The unauthenticated LAN session request should return `401`. Open
`http://localhost:7317` on the PC to verify that existing Herdr App tabs appear.
If the relay was previously enabled, turn off **Let this PC be reached from anywhere** in the
**Ovis relay** tab. Disabling the account service in the unit does not turn off an already enabled
relay.

## Pair Android and install the web app

1. Transfer `Ovis-Home-WiFi.crt` from WSL to the phone. In Android settings, find
   **Install a certificate > CA certificate** and confirm that **Ovis Home Wi-Fi** is listed
   under user certificates.
2. On the PC at `http://localhost:7317`, open **Settings > Devices and PCs > Add a connection >
   Phones and devices > Without signing in (same Wi-Fi · Tailscale)**. Read the six digits under
   **Pairing code**. In Ovis 0.5.9, **Direct** connects the current client to another PC.
   The alphanumeric code in **Ovis relay** belongs to the cloud pairing flow.
3. On the phone, open `https://YOUR_WSL_LAN_IPV4:7318` in Chrome and enter the six digits. Include
   `https://` explicitly. Open an existing Herdr tab to confirm the connection.
4. To install the PWA, wait until Chrome accepts the HTTPS connection, then use
   **⋮ > Install and create shortcut > Install** (the menu label can vary). If the tab was
   already open when you imported the CA, fully stop and reopen Chrome and load the address
   in a fresh tab. If the warning remains, read the specific Chrome error. Compare the phone's CA
   fingerprint with `openssl x509 -in ~/.config/ovis/tls/root.crt -noout -fingerprint -sha256`.
   Also check the IP and certificate dates. Proceeding through a certificate warning can open
   the tab, while service workers and PWA installation require trusted HTTPS. Pairing and PWA
   installation were verified on Android. Fully restarting Chrome after importing the CA
   resolved the installation issue.
   [Ovis LAN HTTPS guide](https://github.com/devswha/ovis/blob/v0.5.9/docs/guide.md#on-your-lan-over-https) ·
   [Chrome web app installation](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DAndroid)

The [Ovis Android app](https://play.google.com/store/apps/details?id=sh.ovis.app) also offers direct
SSH as a separate option. For LAN use it needs a reachable SSH server with key authentication.
An SSH listener bound to loopback is reachable only from the host. The HTTPS setup above uses
no SSH listener on the LAN. If replacing Collie, remove its own service, dedicated proxy or VPN
service, data and old Android CA after verifying Ovis. Keep any shared proxy or VPN used by other
services.
