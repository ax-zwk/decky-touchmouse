#!/usr/bin/env python3
"""touchmouse — el touchscreen de una handheld como mouse SOLO DE MOVIMIENTO.

Diseño: lo más simple y seguro posible.
- El touch NUNCA genera clicks: solo mueve el cursor (EV_REL X/Y). No hay
  tap-click, ni drag, ni scroll, ni gestos. Los clicks se hacen con los
  botones físicos del dispositivo.
- Sin daemon externo, sin InputPlumber, sin perfiles por dispositivo, sin red,
  sin subprocess. Solo stdlib de Python 3.
- Solo toca dos dispositivos: lee el touchscreen y escribe un mouse virtual
  (uinput). Al detenerse (o morir el proceso) libera el touch y borra el mouse:
  el kernel suelta el EVIOCGRAB y destruye el device uinput al cerrarse los fd.

Uso CLI:
    sudo python3 touchmouse.py                 # autodetecta, modo mouse
    sudo python3 touchmouse.py --list          # dispositivos y rangos
    sudo python3 touchmouse.py --swap-xy --flip-y   # calibración de ejes
    sudo python3 touchmouse.py --start-off     # arranca en touchscreen;
                                               # kill -USR1 <pid> alterna
    python3 touchmouse.py --self-test          # tests de lógica, sin hardware

Como motor del plugin Decky "TouchMouse": main.py importa la clase Engine.
"""

import argparse
import fcntl
import glob
import os
import random
import select
import signal
import struct
import sys
import threading
import time

# ---------------- constantes Linux input/uinput (verificadas contra headers del kernel) ----------------

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT, SYN_DROPPED = 0, 3
REL_X, REL_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT = 0x110, 0x111   # solo se DECLARAN (udev clasifica mouse); nunca se emiten
BTN_SOUTH = 0x130                    # para listar gamepads en --list (informativo)
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36
ABS_MT_TRACKING_ID = 0x39

def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr

EVIOCGRAB = _ioc(1, 'E', 0x90, 4)                 # 0x40044590
def EVIOCGBIT(ev, size): return _ioc(2, 'E', 0x20 + ev, size)
def EVIOCGABS(axis):     return _ioc(2, 'E', 0x40 + axis, 24)
def EVIOCGNAME(size):    return _ioc(2, 'E', 0x06, size)

UI_SET_EVBIT  = _ioc(1, 'U', 100, 4)              # 0x40045564
UI_SET_KEYBIT = _ioc(1, 'U', 101, 4)
UI_SET_RELBIT = _ioc(1, 'U', 102, 4)
UI_DEV_CREATE  = _ioc(0, 'U', 1, 0)               # 0x5501
UI_DEV_DESTROY = _ioc(0, 'U', 2, 0)

EVENT = struct.Struct('@llHHi')                   # struct input_event (24 bytes en 64-bit)
ABSINFO = struct.Struct('@6i')                    # struct input_absinfo
# struct uinput_user_dev: name[80] + input_id(4xu16) + ff_effects_max(u32) + 4xabs[64](i32) = 1116
UINPUT_USER_DEV = struct.Struct('@80sHHHHi' + 'i' * 256)
BUS_VIRTUAL = 0x06

DEFAULT_SETTINGS = {
    'touch': '',            # nodo evdev del touchscreen; '' = autodetectar
    'speed': 1.6,           # multiplicador base de velocidad
    'accel': 0.10,          # aceleración por velocidad del dedo (0 = off)
    'swap_xy': False,       # calibración: panel montado en vertical
    'flip_x': False,
    'flip_y': False,
}

# ---------------- traducción touch -> movimiento (lógica pura, testeable sin hardware) ----------------

class Finger:
    __slots__ = ('x', 'y', 'pend_dx', 'pend_dy', 'started')
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.pend_dx = self.pend_dy = 0.0
        self.started = False   # superó el umbral anti-jitter y ya emite movimiento

class Translator:
    """Eventos evdev del touchscreen -> SOLO (EV_REL, REL_X/REL_Y, delta).

    No existe ningún camino de código que emita EV_KEY o wheel.
    El dedo de menor slot mueve el cursor; los demás dedos se ignoran.
    """

    def __init__(self, transform=lambda dx, dy: (dx, dy), speed=1.6, accel=0.10,
                 move_thresh=8):
        self.transform = transform
        self.speed, self.accel = speed, accel
        self.move_thresh = move_thresh
        self.reset()

    def reset(self):
        self.slots = {}          # slot -> [tracking_id, x, y]
        self.cur_slot = 0
        self.fingers = {}        # slot -> Finger activos
        self.rem_x = self.rem_y = 0.0

    def feed(self, events, out):
        for typ, code, val in events:
            if typ == EV_ABS:
                if code == ABS_MT_SLOT:
                    self.cur_slot = val
                elif code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID):
                    s = self.slots.setdefault(self.cur_slot, [-1, 0, 0])
                    if code == ABS_MT_TRACKING_ID: s[0] = val
                    elif code == ABS_MT_POSITION_X: s[1] = val
                    else: s[2] = val
            elif typ == EV_SYN:
                if code == SYN_DROPPED:
                    self.reset()
                elif code == SYN_REPORT:
                    self._frame(out)

    def _frame(self, out):
        active = {s: v for s, v in self.slots.items() if v[0] >= 0}
        prev = self.fingers
        stay = [s for s in sorted(active) if s in prev]
        if stay:
            f = prev[stay[0]]
            _, x, y = active[stay[0]]
            dx, dy = x - f.x, y - f.y
            f.x, f.y = x, y
            f.pend_dx += dx
            f.pend_dy += dy
            if not f.started and abs(f.pend_dx) + abs(f.pend_dy) > self.move_thresh:
                f.started = True
            if f.started and (f.pend_dx or f.pend_dy):
                self._emit(f.pend_dx, f.pend_dy, out)
                f.pend_dx = f.pend_dy = 0.0
        for s in list(prev):
            if s not in active:
                del prev[s]
        for s in active:
            if s not in prev:
                prev[s] = Finger(active[s][1], active[s][2])

    def _emit(self, dx, dy, out):
        dx, dy = self.transform(dx, dy)
        dist = (dx * dx + dy * dy) ** 0.5
        factor = self.speed * (1.0 + self.accel * min(dist, 60.0) / 6.0)
        self.rem_x += dx * factor
        self.rem_y += dy * factor
        ix, iy = int(self.rem_x), int(self.rem_y)
        self.rem_x -= ix
        self.rem_y -= iy
        if ix: out.append((EV_REL, REL_X, ix))
        if iy: out.append((EV_REL, REL_Y, iy))

# ---------------- acceso a dispositivos ----------------

def evdev_name(fd):
    buf = bytearray(256)
    try:
        fcntl.ioctl(fd, EVIOCGNAME(256), buf)
    except OSError:
        return ''
    return buf.split(b'\0', 1)[0].decode('utf-8', 'replace')

def has_bit(fd, evtype, code):
    size = 8 if evtype == EV_ABS else 96
    buf = bytearray(size)
    try:
        fcntl.ioctl(fd, EVIOCGBIT(evtype, size), buf)
    except OSError:
        return False
    return bool(buf[code // 8] & (1 << (code % 8)))

def abs_range(fd, axis):
    buf = bytearray(24)
    try:
        fcntl.ioctl(fd, EVIOCGABS(axis), buf)
    except OSError:
        return None
    val, mn, mx, fuzz, flat, res = ABSINFO.unpack(buf)
    return mn, mx

def scan_devices():
    infos = []
    for path in sorted(glob.glob('/dev/input/event*'),
                       key=lambda p: int(p.rsplit('event', 1)[1])):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            infos.append({
                'path': path, 'name': evdev_name(fd),
                'touch': has_bit(fd, EV_ABS, ABS_MT_POSITION_X),
                'pad': has_bit(fd, EV_KEY, BTN_SOUTH),
                'rx': abs_range(fd, ABS_MT_POSITION_X),
                'ry': abs_range(fd, ABS_MT_POSITION_Y),
            })
        finally:
            os.close(fd)
    return infos

def pick_touchscreen(infos, override=''):
    if override:
        return override
    cands = [i for i in infos if i['touch']]
    if not cands:
        raise RuntimeError('no se encontró touchscreen (ABS_MT_POSITION_X); '
                           'especificar el nodo con --touch')
    return cands[0]['path']

def make_transform(s):
    sx = -1 if s.get('flip_x') else 1
    sy = -1 if s.get('flip_y') else 1
    if s.get('swap_xy'):
        return lambda dx, dy: (sy * dy, sx * dx)
    return lambda dx, dy: (sx * dx, sy * dy)

def make_translator(s, tinfo=None):
    trans = Translator(transform=make_transform(s),
                       speed=float(s.get('speed', 1.6)),
                       accel=float(s.get('accel', 0.10)))
    # umbral anti-jitter proporcional al panel (default pensado para ~1080p)
    if tinfo and tinfo.get('rx'):
        scale = max(tinfo['rx'][1], (tinfo.get('ry') or (0, 1080))[1]) / 1080.0
        trans.move_thresh = max(2, int(8 * scale))
    return trans

def create_mouse(dry=False):
    """Mouse virtual de SOLO movimiento. Declara BTN_LEFT/RIGHT para que udev lo
    clasifique como mouse, pero este plugin jamás emite eventos de botón."""
    if dry:
        return None
    path = '/dev/uinput' if os.path.exists('/dev/uinput') else '/dev/input/uinput'
    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for b in (BTN_LEFT, BTN_RIGHT):
            fcntl.ioctl(fd, UI_SET_KEYBIT, b)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
        for r in (REL_X, REL_Y):
            fcntl.ioctl(fd, UI_SET_RELBIT, r)
        dev = UINPUT_USER_DEV.pack(b'TouchMouse Virtual Mouse',
                                   BUS_VIRTUAL, 0x1234, 0x5678, 1, 0,
                                   *([0] * 256))
        os.write(fd, dev)
        fcntl.ioctl(fd, UI_DEV_CREATE)
    except OSError:
        os.close(fd)
        raise
    time.sleep(0.3)   # que udev/libinput lo adopten antes de usarlo
    return fd

def write_events(fd, events, dry=False, log=None):
    if not events:
        return
    if dry:
        for e in events:
            (log or print)('mouse: %s' % (e,))
        return
    buf = b''.join(EVENT.pack(0, 0, t, c, v) for t, c, v in events)
    buf += EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0)
    os.write(fd, buf)

def grab(fd, on):
    fcntl.ioctl(fd, EVIOCGRAB, 1 if on else 0)

def read_events(fd):
    try:
        data = os.read(fd, EVENT.size * 64)
    except BlockingIOError:
        return []
    if not data:
        return []
    return [EVENT.unpack_from(data, off)[2:]
            for off in range(0, len(data) - EVENT.size + 1, EVENT.size)]

# ---------------- motor (CLI y plugin Decky) ----------------

class Engine:
    """Hilo que captura el touchscreen y emite movimiento de mouse.

    start()/stop() bloqueantes y rápidos. set_mode()/update_settings() son
    pedidos que el hilo consume en su loop. Nada de red, subprocess ni otros
    dispositivos: solo el touchscreen (lectura) y uinput (escritura).
    """

    def __init__(self, settings=None, log=None, dry=False):
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.log = log or (lambda m: print(m, flush=True))
        self.dry = dry
        self._thread = None
        self._stop = threading.Event()
        self._mode_on = False
        self._mode_req = None
        self._settings_ver = 0
        self._applied_ver = -1
        self._error = None
        self._touch_path = None
        self._touch_name = None

    def start(self, mouse=True):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._error = None
        self._mode_req = bool(mouse)
        self._settings_ver += 1
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='touchmouse')
        self._thread.start()
        for _ in range(60):   # hasta 3 s esperando init o error
            if self._touch_path or self._error or not self._thread.is_alive():
                break
            time.sleep(0.05)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._mode_on = False
        self._touch_path = None
        self._touch_name = None

    def set_mode(self, on):
        self._mode_req = bool(on)

    def toggle(self):
        self._mode_req = not self._mode_on

    def update_settings(self, settings):
        self.settings.update(settings)
        self._settings_ver += 1

    def status(self):
        running = bool(self._thread and self._thread.is_alive())
        return {
            'running': running,
            'mode': 'mouse' if (running and self._mode_on) else 'touchscreen',
            'touch_path': self._touch_path,
            'touch_name': self._touch_name,
            'error': self._error if not running else None,
            'pid': os.getpid(),
        }

    def _run(self):
        s = self.settings
        touch_fd = mouse_fd = None
        try:
            infos = scan_devices()
            touch_path = pick_touchscreen(infos, s.get('touch', ''))
            tinfo = next((i for i in infos if i['path'] == touch_path), None)
            self._touch_path = touch_path
            self._touch_name = tinfo['name'] if tinfo else '?'
            self.log(f"touchscreen: {touch_path} ({self._touch_name})")
            trans = make_translator(s, tinfo)
            mouse_fd = create_mouse(dry=self.dry)
            touch_fd = os.open(touch_path, os.O_RDONLY | os.O_NONBLOCK)
            self._applied_ver = self._settings_ver
            self._mode_on = False
            if self._mode_req and not self.dry:
                grab(touch_fd, True)
                self._mode_on = True
            self.log(f"listo: modo {'mouse' if self._mode_on else 'touchscreen'} "
                     f"(pid {os.getpid()})")

            while not self._stop.is_set():
                if self._settings_ver != self._applied_ver:
                    trans = make_translator(self.settings, tinfo)
                    self._applied_ver = self._settings_ver
                if self._mode_req is not None and self._mode_req != self._mode_on:
                    self._mode_on = self._mode_req
                    if not self.dry:
                        grab(touch_fd, self._mode_on)
                    trans.reset()
                    read_events(touch_fd)   # descartar lo acumulado
                    self._mode_req = None
                    self.log('modo ' + ('MOUSE (touch capturado)' if self._mode_on
                                        else 'TOUCHSCREEN normal'))
                r, _, _ = select.select([touch_fd], [], [], 0.2)
                if touch_fd in r:
                    evts = read_events(touch_fd)
                    if self._mode_on and evts:
                        out = []
                        trans.feed(evts, out)
                        write_events(mouse_fd, out, dry=self.dry, log=self.log)
        except Exception as e:
            self._error = str(e)
            self.log(f'error: {e}')
        finally:
            # Pase lo que pase: liberar el touch y borrar el mouse virtual.
            if touch_fd is not None:
                if not self.dry:
                    try: grab(touch_fd, False)
                    except OSError: pass
                os.close(touch_fd)
            if mouse_fd is not None:
                try: fcntl.ioctl(mouse_fd, UI_DEV_DESTROY)
                except OSError: pass
                os.close(mouse_fd)
            self._mode_on = False
            self.log('touchscreen liberado, mouse virtual eliminado')

# ---------------- tests sin hardware ----------------

def self_test():
    def frame(T, evts, out):
        T.feed(evts + [(EV_SYN, SYN_REPORT, 0)], out)

    def down(slot, tid, x, y):
        return [(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, tid),
                (EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y)]

    def move(slot, x, y):
        return [(EV_ABS, ABS_MT_SLOT, slot),
                (EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y)]

    def up(slot):
        return [(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, -1)]

    T = Translator(speed=1.0, accel=0.0)
    out = []
    # 1) tap (y cualquier combinación de toques) no produce NADA
    frame(T, down(0, 1, 500, 300), out)
    frame(T, up(0), out)
    frame(T, down(0, 2, 400, 300), out)
    frame(T, down(1, 3, 600, 300), out)
    frame(T, up(0), out)
    frame(T, up(1), out)
    assert out == [], out
    # 2) deslizar produce movimiento; jamás EV_KEY ni wheel
    frame(T, down(0, 4, 500, 300), out)
    frame(T, move(0, 560, 340), out)
    frame(T, move(0, 620, 380), out)
    frame(T, up(0), out)
    assert sum(e[2] for e in out if e[1] == REL_X) == 120, out
    assert sum(e[2] for e in out if e[1] == REL_Y) == 80, out
    assert all(e[0] == EV_REL and e[1] in (REL_X, REL_Y) for e in out), out
    out.clear()
    # 3) dos dedos: solo el primero mueve; sin clicks
    frame(T, down(0, 5, 400, 300), out)
    frame(T, down(1, 6, 600, 300), out)
    frame(T, move(0, 400, 380) + move(1, 600, 900), out)
    frame(T, up(0) + up(1), out)
    assert any(e[1] == REL_Y for e in out), out
    assert all(e[0] == EV_REL and e[1] in (REL_X, REL_Y) for e in out), out
    out.clear()
    # 4) transform de ejes (panel vertical)
    T2 = Translator(transform=lambda dx, dy: (dy, -dx), speed=1.0, accel=0.0)
    o2 = []
    frame(T2, down(0, 7, 100, 100), o2)
    frame(T2, move(0, 150, 100), o2)
    assert (EV_REL, REL_Y, -50) in o2, o2
    # 5) fuzz: 2000 frames aleatorios, NUNCA un evento que no sea REL X/Y
    rng = random.Random(42)
    T3 = Translator()
    o3 = []
    for _ in range(2000):
        evts = []
        for _ in range(rng.randint(1, 6)):
            slot = rng.randint(0, 4)
            evts.append((EV_ABS, ABS_MT_SLOT, slot))
            kind = rng.randint(0, 2)
            if kind == 0:
                evts.append((EV_ABS, ABS_MT_TRACKING_ID, rng.choice([-1, rng.randint(0, 99)])))
            else:
                evts.append((EV_ABS, rng.choice([ABS_MT_POSITION_X, ABS_MT_POSITION_Y]),
                             rng.randint(0, 1920)))
        frame(T3, evts, o3)
    assert all(e[0] == EV_REL and e[1] in (REL_X, REL_Y) for e in o3), 'evento prohibido en fuzz'
    # 6) thresholds escalan con el panel
    t = make_translator(DEFAULT_SETTINGS, {'rx': (0, 2160), 'ry': (0, 3840)})
    assert t.move_thresh > 8
    print('self-test: PASS (tap=nada, solo REL X/Y, ejes, fuzz 2000 frames, escalado)')

# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--touch', default='', help='nodo evdev del touchscreen (default: autodetectar)')
    ap.add_argument('--speed', type=float, default=1.6, help='velocidad base (default 1.6)')
    ap.add_argument('--accel', type=float, default=0.10, help='aceleración (default 0.10, 0=off)')
    ap.add_argument('--swap-xy', action='store_true', help='intercambiar ejes X/Y del panel')
    ap.add_argument('--flip-x', action='store_true', help='invertir X de pantalla')
    ap.add_argument('--flip-y', action='store_true', help='invertir Y de pantalla')
    ap.add_argument('--start-off', action='store_true', help='arrancar en modo touchscreen')
    ap.add_argument('--dry-run', action='store_true',
                    help='no crea mouse ni captura el touch: imprime lo que haría')
    ap.add_argument('--list', action='store_true', help='listar dispositivos de entrada y salir')
    ap.add_argument('--self-test', action='store_true', help='tests de lógica sin hardware')
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return
    if args.list:
        for i in scan_devices():
            tags = ('TOUCH' if i['touch'] else '') + (' PAD' if i['pad'] else '')
            print(f"{i['path']:22s} {i['name']:40s} {tags}")
            if i['touch']:
                print(f"    rangos: X={i['rx']} Y={i['ry']}")
        return
    if os.geteuid() != 0 and not args.dry_run:
        sys.exit('necesita root: sudo python3 touchmouse.py')

    settings = {'touch': args.touch, 'speed': args.speed, 'accel': args.accel,
                'swap_xy': args.swap_xy, 'flip_x': args.flip_x, 'flip_y': args.flip_y}
    engine = Engine(settings, dry=args.dry_run)
    signal.signal(signal.SIGUSR1, lambda *_: engine.toggle())
    engine.start(mouse=not args.start_off)
    st = engine.status()
    if st['error']:
        sys.exit(f"error: {st['error']}")
    print(f"kill -USR1 {os.getpid()} alterna mouse/touchscreen; Ctrl-C sale", flush=True)
    try:
        while engine.status()['running']:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()

if __name__ == '__main__':
    main()
