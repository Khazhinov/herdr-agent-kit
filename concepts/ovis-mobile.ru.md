# Ovis для доступа к Herdr App с телефона

[English](ovis-mobile.md) | Русский

[Ovis](https://github.com/devswha/ovis) открывает уже запущенные пространства, вкладки и терминалы
агентов Herdr App. Эта схема проверена с сервером Ovis 0.5.9 и `hc` 0.1.16 в WSL2 с зеркальной
сетью Windows. Сервер работает как пользовательская служба и подключается к тому же ядру, что и
Herdr App. На компьютере Ovis доступен по HTTP на loopback, а на телефоне в домашней сети по
отдельному HTTPS-адресу. Этот адрес требует сопряжения с первого подключения.

Для доступа вне дома есть [облачный Ovis relay](https://github.com/devswha/ovis/blob/v0.5.9/docs/guide.md#through-the-ovis-relay)
и [Tailscale](https://github.com/devswha/ovis/blob/v0.5.9/docs/guide.md#through-tailscale).
В Android на пользователя или профиль может работать только одна служба VPN. Запуск Tailscale
может остановить уже подключённый VPN-клиент, например v2rayNG. Доступ по локальному HTTPS и
облачный Ovis relay обходятся без VPN-профиля на телефоне. Локальный режим подходит, если
достаточно доступа из дома и нужно сохранить действующий VPN. Если он направляет трафик
локальной сети в туннель, добавьте для адреса Ovis прямой маршрут в настройках VPN.
[Документация Android о VPN](https://developer.android.com/develop/connectivity/vpn)

## Установка в WSL, где работают терминалы

Войдите под пользователем Linux, который уже видит терминалы Herdr App через `hc ls`. Проверьте
`hc --version`, `systemctl --user status` и `ip -br -4 addr` в этом дистрибутиве WSL. Для команд
нужны x86-64, Bash, OpenSSL, `gh` и работающий пользовательский systemd. Возьмите IPv4 интерфейса,
доступного из домашней сети. В зеркальном режиме это может быть тот же адрес, что у Windows.
Штатный порт Ovis для loopback - 7317. Для HTTPS в домашней сети здесь используется 7318.
Если HTTPS отвечает из WSL, а телефон не может подключиться, проверьте зеркальный режим и правила
файрвола по [документации WSL](https://learn.microsoft.com/windows/wsl/networking#mirrored-mode-networking).

Скачайте файлы указанного выпуска и проверьте SHA-256 перед распаковкой. Ovis Desktop запускает
собственное ядро. Для существующих терминалов Herdr App используйте Linux-сервер из
[выпуска Ovis 0.5.9](https://github.com/devswha/ovis/releases/tag/v0.5.9) с
`OVIS_RUNTIME=herdr-core`.

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

## Настройка HTTPS и запуск службы

Подставьте адрес домашней сети вместо `YOUR_WSL_LAN_IPV4`. Команды рассчитаны на первую настройку
TLS. Закрытый ключ CA храните в WSL, на телефон переносите только открытый сертификат CA. Если
LAN-адрес изменится, обновите адрес в службе и перевыпустите серверный сертификат с новым IP в
subject alternative name. Сохраните тот же CA, чтобы настройка доверия на Android продолжила
работать. В примере серверный сертификат действует год.

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

Сохраните службу в `~/.config/systemd/user/ovis.service`. При необходимости создайте каталог.
Подставьте LAN-адрес ещё раз. В примере `hc` установлен в `~/.local/bin`. Если он находится в
другом месте, возьмите абсолютный путь из `command -v hc`. Для именованной сессии ядра или
нестандартного сокета укажите `HC_SESSION` или `HC_SOCKET`, которые использует текущий Herdr App.

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

В ответе проверки должны быть `runtime: herdr-core`, разрешённое подключение к терминалам и
состояние relay `off`. Запрос к API сессий по LAN без сопряжения должен вернуть `401`.
Откройте `http://localhost:7317` на компьютере и проверьте, что видны существующие вкладки Herdr App.
Если relay уже включали, выключите **Let this PC be reached from anywhere** на вкладке **Ovis relay**.
Отключение службы аккаунтов в параметрах запуска само по себе не выключает ранее запущенный relay.

## Сопряжение Android и установка веб-приложения

1. Перенесите `Ovis-Home-WiFi.crt` из WSL на телефон. В Android найдите
   **Установить сертификат > Сертификат CA** и убедитесь, что **Ovis Home Wi-Fi** появился в
   пользовательских сертификатах.
2. На компьютере откройте `http://localhost:7317` и выберите **Settings > Devices and PCs >
   Add a connection > Phones and devices > Without signing in (same Wi-Fi · Tailscale)**.
   В разделе **Pairing code** появятся шесть цифр. В Ovis 0.5.9 вкладка **Direct** подключает
   текущий клиент к другому ПК. Буквенно-цифровой код в **Ovis relay** используется для облачного
   сопряжения.
3. На телефоне откройте `https://YOUR_WSL_LAN_IPV4:7318` в Chrome и введите шесть цифр.
   Явно укажите `https://`. Откройте существующую вкладку Herdr для проверки.
4. Когда Chrome примет HTTPS, установите PWA через **⋮ > Установить приложение**. Название пункта
   зависит от версии Chrome. Если вкладка была открыта при импорте сертификата CA, полностью
   остановите Chrome, запустите снова и откройте адрес в новой вкладке. Если предупреждение
   осталось, посмотрите текст ошибки Chrome. Сверьте отпечаток CA на телефоне с выводом
   `openssl x509 -in ~/.config/ovis/tls/root.crt -noout -fingerprint -sha256`, а также проверьте
   IP-адрес и срок сертификата. Через предупреждение можно открыть вкладку, но сервис-воркерам
   и установке PWA нужен доверенный HTTPS. Сопряжение и установка PWA проверены на Android.
   В нашем случае PWA установилась после полного перезапуска Chrome с уже импортированным CA.
   [HTTPS в локальной сети в документации Ovis](https://github.com/devswha/ovis/blob/v0.5.9/docs/guide.md#on-your-lan-over-https) ·
   [Установка веб-приложений в Chrome](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DAndroid&hl=ru)

[Приложение Ovis для Android](https://play.google.com/store/apps/details?id=sh.ovis.app) также
поддерживает прямое подключение по SSH. Для этого нужен доступный по локальной сети SSH-сервер
с входом по ключу. SSH, привязанный к loopback, доступен только с компьютера. Для описанной
выше схемы с HTTPS входящий SSH не нужен. При переходе с Collie удалите его службу, выделенный
прокси или VPN-службу, данные и старый CA на Android после проверки Ovis. Общий прокси или VPN,
которым пользуются другие сервисы, оставьте.
