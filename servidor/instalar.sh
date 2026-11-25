#!/usr/bin/env bash
# =============================================================================
#  Notificador DGRDT - instala el agente en un servidor Ubuntu en la nube
#  (probado en Oracle Cloud "Always Free", Ubuntu 24.04 ARM).
#
#  Uso (en el servidor):   bash instalar.sh <nombre-publico> <clave>
#    nombre-publico  ej. 129-151-10-20.sslip.io  (apunta solo a la IP; HTTPS automático)
#    clave           para entrar desde la web oficial y a la ventana remota
#
#  Qué queda andando (todo arranca solo al prender el servidor):
#    - pantalla virtual (Xvfb :1) + administrador de ventanas (openbox)
#    - el navegador del agente (Chromium) en esa pantalla
#    - ventana remota por web (noVNC) en https://<nombre>/vnc/ con la clave
#    - el agente en 127.0.0.1:8765, publicado por HTTPS (Caddy) en https://<nombre>/
#  Se puede volver a correr para actualizar: no borra sesiones, casos ni ajustes.
# =============================================================================
set -euo pipefail
HOST="${1:?Falta el nombre público, por ejemplo 1-2-3-4.sslip.io}"
CLAVE="${2:?Falta la clave}"
USUARIO="$(id -un)"
APP="$HOME/notificador-dgrdt"
BASE="$HOME/NotificadorDGRDT"
VENV="$BASE/venv"
REPO="https://github.com/DevSalemAli/notificador-dgrdt.git"
export DEBIAN_FRONTEND=noninteractive

echo "== 1/8 Sistema: hora de Argentina, idioma y paquetes"
sudo timedatectl set-timezone America/Argentina/Buenos_Aires
echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | sudo debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | sudo debconf-set-selections
sudo apt-get update -y -q
sudo apt-get install -y -q --no-install-recommends \
  git python3-venv python3-pip xvfb x11vnc openbox novnc websockify caddy \
  fonts-liberation fonts-dejavu-core fonts-noto-core fonts-noto-color-emoji \
  locales iptables-persistent netfilter-persistent ca-certificates curl
sudo locale-gen es_AR.UTF-8 >/dev/null
# Chromium necesita espacios de nombres sin privilegios para su "sandbox" (Ubuntu 24.04 los restringe)
echo "kernel.apparmor_restrict_unprivileged_userns=0" | sudo tee /etc/sysctl.d/60-notificador.conf >/dev/null
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 >/dev/null 2>&1 || true

echo "== 2/8 Programa (GitHub)"
if [ -d "$APP/.git" ]; then git -C "$APP" pull --ff-only -q; else git clone -q --depth 1 "$REPO" "$APP"; fi

echo "== 3/8 Python y librerías"
mkdir -p "$BASE"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q playwright==1.65.2 python-docx==1.2.0 openpyxl==3.1.5 pypdf==7.1.1

echo "== 4/8 Navegador (Chromium) y lo que necesita"
"$VENV/bin/python" -m playwright install chromium
sudo "$VENV/bin/python" -m playwright install-deps chromium >/dev/null

echo "== 5/8 Clave"
( umask 077; printf '%s' "$CLAVE" > "$BASE/clave_remota.txt" )
HASH="$(caddy hash-password --plaintext "$CLAVE")"

echo "== 6/8 Servicios"
servicio() {   # nombre, descripción, después-de, comando, entorno extra
  sudo tee "/etc/systemd/system/$1.service" >/dev/null <<EOF
[Unit]
Description=Notificador DGRDT: $2
After=network-online.target $3
Wants=network-online.target

[Service]
User=$USUARIO
WorkingDirectory=$APP/agente
Environment=DISPLAY=:1
Environment=TZ=America/Argentina/Buenos_Aires
Environment=LANG=es_AR.UTF-8
$5
ExecStart=$4
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
}
servicio notificador-pantalla "pantalla virtual" "" "/usr/bin/Xvfb :1 -screen 0 1600x1000x24 -nolisten tcp" ""
servicio notificador-ventanas "administrador de ventanas" "notificador-pantalla.service" "/usr/bin/openbox" ""
servicio notificador-vnc "ventana remota (VNC, solo local)" "notificador-pantalla.service" \
  "/usr/bin/x11vnc -display :1 -localhost -rfbport 5901 -forever -shared -nopw -quiet -noxdamage" ""
servicio notificador-novnc "ventana remota por web (solo local)" "notificador-vnc.service" \
  "/usr/bin/websockify --web /usr/share/novnc 127.0.0.1:6080 127.0.0.1:5901" ""
servicio notificador-agente "agente" "notificador-ventanas.service" \
  "$VENV/bin/python $APP/agente/agente.py --sin-web --log" \
  "Environment=NOTIFICADOR_HOSTS=$HOST
Environment=PYTHONUNBUFFERED=1"

# HTTPS (certificado automático) + ventana remota protegida con la clave
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$HOST {
	encode gzip
	handle_path /vnc/* {
		basicauth {
			notificador $HASH
		}
		reverse_proxy 127.0.0.1:6080
	}
	handle {
		reverse_proxy 127.0.0.1:8765
	}
}
EOF

echo "== 7/8 Firewall del servidor: abrir 80 y 443"
for p in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$p" -m state --state NEW -j ACCEPT 2>/dev/null; then
    n="$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT/{print $1; exit}')"
    if [ -n "$n" ]; then sudo iptables -I INPUT "$n" -p tcp --dport "$p" -m state --state NEW -j ACCEPT
    else sudo iptables -A INPUT -p tcp --dport "$p" -m state --state NEW -j ACCEPT; fi
  fi
done
sudo netfilter-persistent save >/dev/null 2>&1 || true

echo "== 8/8 Arranque"
sudo systemctl daemon-reload
sudo systemctl enable -q notificador-pantalla notificador-ventanas notificador-vnc notificador-novnc notificador-agente caddy
sudo systemctl restart notificador-pantalla
sleep 2
sudo systemctl restart notificador-ventanas notificador-vnc notificador-novnc notificador-agente caddy
sleep 4
curl -s -o /dev/null -w "agente: %{http_code}\n" -H "Host: $HOST" http://127.0.0.1:8765/ || true
echo "Listo: https://$HOST  (ventana remota: https://$HOST/vnc/vnc.html)"
