# Mi Reachy Portal
Portal web propio que lista y abre todas las apps del robot (hub, audio_mixer,
push-to-talk y tus apps propias) desde cualquier dispositivo de la misma red.

## Estructura
```
mi_portal/
├─ portal/
│  ├─ daemon.py         # FastAPI en 0.0.0.0:8090
│  └─ static/           # SPA (index.html, app.js, style.css)
├─ portal_config.json   # Registro de apps (edita aquí para añadir/quitar)
└─ launcher.py          # Arranca el portal
```

## Registro de apps
Edita `portal_config.json`. Cada app tiene:
- `slug` (id único), `nombre` (etiqueta), `tipo` ("pollen" o "propia"),
- `url` interna (`http://127.0.0.1:<puerto>`), `icono` (emoji).

El portal reescribe la URL con la IP LAN del robot y marca cada app como
"Abierta" o "Cerrada" según responda.

## Uso (en el robot)
```
cd /home/pollen/mi_portal
/venvs/class_report_venv/bin/python launcher.py
```
Abre desde cualquier dispositivo: `http://<ip-del-robot>:8090`

## Autoinicio (opcional)
Crea `/etc/systemd/system/mi-portal.service`:
```
[Unit]
Description=Mi Reachy Portal
After=network-online.target
[Service]
WorkingDirectory=/home/pollen/mi_portal
ExecStart=/venvs/class_report_venv/bin/python launcher.py
Restart=always
User=pollen
[Install]
WantedBy=multi-user.target
```
Luego: `sudo systemctl enable --now mi-portal`