# TouchMouse (Decky plugin)

Convierte el touchscreen de una handheld en un **mouse de solo movimiento**:
deslizás el dedo y se mueve el cursor. **Nunca genera clicks** — los clicks se
hacen con los botones físicos del dispositivo. Pensado para la AYN Odin 3 con
Armada OS, pero no tiene perfiles por dispositivo: funciona en cualquier
handheld Linux con Decky Loader, touchscreen y `/dev/uinput`.

No usa daemon externo, ni InputPlumber, ni unidades systemd, ni configuración
del sistema. Se instala, activa, desactiva y desinstala desde Decky.

## Diseño simple y seguro

- El traductor solo emite `EV_REL X/Y`. No existe camino de código que emita
  botones o scroll (verificado con tests + fuzz de 2000 frames).
- Solo abre dos dispositivos: el touchscreen (lectura) y `/dev/uinput`
  (escritura). No lee el mando, no usa red, no ejecuta subprocess ni shell.
- Escribe un único archivo: `settings.json` en la carpeta de settings del
  plugin (`~/homebrew/settings/decky-touchmouse/`).
- Al apagar el toggle, desactivar o desinstalar el plugin desde Decky, se
  libera el touchscreen y se elimina el mouse virtual. Si el proceso muere,
  el kernel libera el grab y destruye el device uinput al cerrarse los fd:
  la pantalla nunca queda capturada.
- Settings validados y acotados (velocidad 0.2–4, etc.); claves desconocidas
  se rechazan.
- Flag `root` en plugin.json: el backend necesita root para evdev/uinput
  (Decky ya corre como root; no instala nada fuera de su carpeta).

## Instalar

1. Descargar el ZIP de la [última release](../../releases/latest) al device:
   ```bash
   curl -LO https://github.com/ax-zwk/decky-touchmouse/releases/latest/download/decky-touchmouse.zip
   ```
   (o pasarlo por SCP/USB como cualquier archivo).
2. En Decky: **Settings → Developer mode ON → Developer → Install Plugin from
   ZIP File** → elegir `decky-touchmouse.zip`.
3. Menú rápido (QAM) → **TouchMouse** → toggle **Pantalla como mouse**.
   Se puede abrir el QAM en pleno juego y alternar ahí.

Desinstalar: Decky → Settings → Plugins → TouchMouse → Uninstall. La pantalla
vuelve a la normalidad al instante.

**Actualizaciones:** ver [UPDATES.md](UPDATES.md) — store personalizado de
Decky para actualizar con un click.

## Calibración

Si el cursor se mueve en el eje equivocado o invertido (paneles montados en
vertical), usar los switches del panel: **Intercambiar ejes X/Y** (si al
deslizar en horizontal el cursor se mueve en vertical), **Invertir
horizontal** y **Invertir vertical** (si el cursor va al revés en ese eje).
Se aplican en caliente.

## Uso como script (sin Decky)

`py_modules/touchmouse.py` también funciona solo:

```bash
sudo python3 touchmouse.py --list          # ver dispositivos
sudo python3 touchmouse.py                 # modo mouse
sudo python3 touchmouse.py --swap-xy --flip-y
sudo python3 touchmouse.py --start-off     # arranca en touchscreen;
                                           # kill -USR1 <pid> alterna
python3 touchmouse.py --self-test          # tests sin hardware
```

## Límites

- Emula un mouse, no los trackpads del Deck vía Steam Input: no hay menús
  radiales ni pad-como-joystick. Sirve para juegos/apps que aceptan mouse.
- Sin gestos: ni tap-click, ni scroll, ni drag (decisión de diseño).
- Probado: lógica de gestos/movimiento, ioctls contra headers del kernel,
  backend con stub de Decky, motor con touch simulado. **Pendiente de prueba
  en hardware real** (Odin 3): calibración de ejes y adopción del mouse por
  gamescope.

## Desarrollo

```bash
npm install
npm run build          # genera dist/index.js
python3 py_modules/touchmouse.py --self-test
python3 test_backend.py
```

MIT.
