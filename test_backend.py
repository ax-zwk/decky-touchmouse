# Test del backend y del motor sin hardware (stub de decky + touch falso por pipe).
import asyncio
import importlib.util
import logging
import os
import sys
import time
import types
from unittest import mock

sys.path.insert(0, 'py_modules')
import touchmouse

logging.basicConfig(level=logging.WARNING)

# ---- stub del módulo decky que inyecta el loader ----
decky = types.ModuleType('decky')
decky.logger = logging.getLogger('decky-test')
decky.DECKY_PLUGIN_SETTINGS_DIR = '/opt/data/cache/scratch/tm-test-settings'
sys.modules['decky'] = decky

spec = importlib.util.spec_from_file_location('plugin_main', 'main.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


async def backend_test():
    p = mod.Plugin()
    await p._main()
    st = await p.get_state()
    assert st['settings']['enabled'] is False
    # validación de settings
    st = await p.set_setting('evil_key', 1)
    assert 'evil_key' not in st['settings']
    st = await p.set_setting('speed', 99)
    assert st['settings']['speed'] == 4.0, st['settings']['speed']
    st = await p.set_setting('speed', 'basura')
    assert st['settings']['speed'] == 4.0
    st = await p.set_setting('touch', '/etc/passwd')
    assert st['settings']['touch'] == ''
    st = await p.set_setting('swap_xy', True)
    assert st['settings']['swap_xy'] is True
    # encender sin hardware: error prolijo, enabled queda False
    st = await p.set_enabled(True)
    assert st['settings']['enabled'] is False, st
    assert st['status']['error'], st
    # persistencia
    saved = __import__('json').load(open(os.path.join(
        decky.DECKY_PLUGIN_SETTINGS_DIR, 'settings.json')))
    assert saved['speed'] == 4.0 and saved['swap_xy'] is True
    await p._unload()
    await p._uninstall()
    print('backend stub (validación, fallo prolijo, persistencia, unload): PASS')


def engine_test():
    r, w = os.pipe()
    os.set_blocking(r, False)   # el engine real abre con O_NONBLOCK
    fake = [{'path': '/dev/input/event99', 'name': 'fake-touch', 'touch': True,
             'pad': False, 'rx': (0, 1080), 'ry': (0, 1920)}]
    logs = []
    eng = touchmouse.Engine({'speed': 1.0, 'accel': 0.0}, log=logs.append, dry=True)

    def ev(t, c, v):
        return touchmouse.EVENT.pack(0, 0, t, c, v)

    def down(tid, x, y):
        return (ev(touchmouse.EV_ABS, touchmouse.ABS_MT_TRACKING_ID, tid)
                + ev(touchmouse.EV_ABS, touchmouse.ABS_MT_POSITION_X, x)
                + ev(touchmouse.EV_ABS, touchmouse.ABS_MT_POSITION_Y, y))

    def move(x, y):
        return (ev(touchmouse.EV_ABS, touchmouse.ABS_MT_POSITION_X, x)
                + ev(touchmouse.EV_ABS, touchmouse.ABS_MT_POSITION_Y, y))

    syn = ev(touchmouse.EV_SYN, touchmouse.SYN_REPORT, 0)
    with mock.patch.object(touchmouse, 'scan_devices', return_value=fake), \
         mock.patch.object(touchmouse.os, 'open', return_value=r):
        eng.start(mouse=True)
        assert eng.status()['running'], eng.status()
        assert eng.status()['mode'] == 'mouse'
        os.write(w, down(1, 500, 300) + syn)
        os.write(w, move(560, 340) + syn + move(620, 380) + syn)
        os.write(w, ev(touchmouse.EV_ABS, touchmouse.ABS_MT_TRACKING_ID, -1) + syn)
        time.sleep(0.6)
        assert any(l.startswith('mouse:') for l in logs), logs
        # toggle a touchscreen y vuelta
        eng.toggle()
        time.sleep(0.4)
        assert eng.status()['mode'] == 'touchscreen'
        eng.toggle()
        time.sleep(0.4)
        assert eng.status()['mode'] == 'mouse'
        # settings en caliente
        eng.update_settings({'swap_xy': True, 'speed': 2.0})
        os.write(w, down(2, 100, 100) + syn + move(150, 100) + syn)
        time.sleep(0.6)
        eng.stop()
    assert not eng.status()['running']
    assert any('liberado' in l for l in logs), logs
    print('engine dry-run (loop, toggle, hot-settings, cleanup): PASS')


asyncio.run(backend_test())
engine_test()
