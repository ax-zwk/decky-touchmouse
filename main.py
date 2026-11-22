"""TouchMouse — plugin Decky: touchscreen como mouse de solo movimiento.

Backend mínimo y seguro:
- Sin red, sin subprocess, sin shell. Solo stdlib + el módulo touchmouse.
- Escribe un único archivo: settings.json en DECKY_PLUGIN_SETTINGS_DIR.
- Un toggle: encendido = captura el touchscreen y emite movimiento de mouse;
  apagado = libera el touchscreen y elimina el mouse virtual.
- _unload/_uninstall detienen el motor: al desactivar o desinstalar el plugin
  desde Decky, la pantalla vuelve a la normalidad. Si el proceso muere, el
  kernel libera el grab y destruye el mouse uinput al cerrarse los fd.
- El touch nunca genera clicks: el motor solo emite EV_REL X/Y.
"""

import asyncio
import json
import os

import decky
import touchmouse

SETTINGS_FILE = 'settings.json'

# clave -> validador. Todo lo que no esté acá se rechaza.
def _num(lo, hi):
    def v(x):
        return max(lo, min(hi, float(x)))
    return v

VALIDATORS = {
    'speed': _num(0.2, 4.0),
    'accel': _num(0.0, 1.0),
    'swap_xy': bool,
    'flip_x': bool,
    'flip_y': bool,
    'touch': lambda x: str(x) if str(x).startswith('/dev/input/event') else '',
}


class Plugin:
    async def _main(self):
        self._engine = None
        self._settings = dict(touchmouse.DEFAULT_SETTINGS)
        self._settings['enabled'] = False
        self._sdir = decky.DECKY_PLUGIN_SETTINGS_DIR
        os.makedirs(self._sdir, exist_ok=True)
        try:
            with open(os.path.join(self._sdir, SETTINGS_FILE), encoding='utf-8') as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k == 'enabled':
                    self._settings['enabled'] = bool(v)
                elif k in VALIDATORS:
                    try:
                        self._settings[k] = VALIDATORS[k](v)
                    except (TypeError, ValueError):
                        pass
        except (OSError, ValueError):
            pass
        if self._settings['enabled']:
            await self._start()
            if self._engine is not None and not self._engine.status()['running']:
                self._settings['enabled'] = False
                self._save()
        decky.logger.info('TouchMouse listo (enabled=%s)', self._settings['enabled'])

    async def _unload(self):
        await self._stop()

    async def _uninstall(self):
        await self._stop()

    # ---- interno ----

    def _save(self):
        try:
            with open(os.path.join(self._sdir, SETTINGS_FILE), 'w', encoding='utf-8') as f:
                json.dump(self._settings, f)
        except OSError as e:
            decky.logger.warning('no pude guardar settings: %s', e)

    async def _start(self):
        eng = touchmouse.Engine(self._settings, log=decky.logger.info)
        await asyncio.to_thread(eng.start)
        self._engine = eng
        st = eng.status()
        if st['error']:
            decky.logger.error('TouchMouse no arrancó: %s', st['error'])

    async def _stop(self):
        if self._engine is not None:
            await asyncio.to_thread(self._engine.stop)
            self._engine = None

    # ---- RPC (llamadas desde el frontend) ----

    async def get_state(self):
        st = self._engine.status() if self._engine else {
            'running': False, 'mode': 'touchscreen', 'desktop_pause': False,
            'touch_path': None, 'touch_name': None, 'error': None,
            'pid': os.getpid(),
        }
        return {'settings': self._settings, 'status': st}

    async def set_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled and self._engine is None:
            await self._start()
        elif not enabled:
            await self._stop()
        self._settings['enabled'] = (enabled and self._engine is not None
                                     and self._engine.status()['running'])
        self._save()
        return await self.get_state()

    async def set_setting(self, key: str, value):
        if key not in VALIDATORS:
            return await self.get_state()
        try:
            value = VALIDATORS[key](value)
        except (TypeError, ValueError):
            return await self.get_state()
        self._settings[key] = value
        self._save()
        if self._engine is not None:
            if key == 'touch':   # cambiar de nodo requiere reiniciar el motor
                await self._stop()
                await self._start()
            else:
                self._engine.update_settings({key: value})
        return await self.get_state()