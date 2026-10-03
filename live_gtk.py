#!/usr/bin/env python3
"""Optional click/hover/drag checks on a fresh isolated Xvfb display."""
import argparse
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from bench import invoke
from grounding import locate_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='deepseek-v4.1-flash:cloud')
    parser.add_argument('--host', default=os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434'))
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--oracle', action='store_true', help='Use known bounds to test input/event recording, no model requests')
    parser.add_argument('--output', type=Path, default=Path('runs') / ('live-' + time.strftime('%Y%m%d-%H%M%S')))
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('repeats must be positive')
    if args.output.exists():
        parser.error('output exists; choose a new path')
    args.output.mkdir(parents=True)
    host = args.host if '://' in args.host else 'http://' + args.host
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1280x800x24', '-nolisten', 'tcp'],
                                stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            import select
            if not select.select([proc.stdout], [], [], 10)[0]:
                raise RuntimeError('Xvfb startup timed out')
            number = proc.stdout.readline().strip()
            if not number.isdigit():
                raise RuntimeError('Xvfb could not allocate a display')
            os.environ.update(DISPLAY=':' + number, GDK_BACKEND='x11', GTK_THEME='Adwaita', NO_AT_BRIDGE='1')
            for key in ['DBUS_SESSION_BUS_ADDRESS', 'AT_SPI_BUS_ADDRESS']:
                os.environ.pop(key, None)
            import gi
            gi.require_version('Gtk', '3.0')
            gi.require_version('Gdk', '3.0')
            from gi.repository import Gtk, Gdk
            xlib = ctypes.CDLL(ctypes.util.find_library('X11'))
            xtst = ctypes.CDLL(ctypes.util.find_library('Xtst'))
            xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]; xlib.XOpenDisplay.restype = ctypes.c_void_p
            xlib.XFlush.argtypes = [ctypes.c_void_p]; xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
            xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
            xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            display = xlib.XOpenDisplay(os.environ['DISPLAY'].encode())
            if not display: raise RuntimeError('Could not connect to isolated display')
            try:
                targets = {'source': (55,125,190,105), 'hover': (485,75,230,85), 'destination': (450,310,260,125)}
                labels = {'source': 'DRAG SOURCE', 'hover': 'HOVER HERE', 'destination': 'DROP TARGET'}
                colors = {'source': (.16,.40,.78), 'hover': (.50,.21,.68), 'destination': (.10,.48,.27)}
                state = {'presses': [], 'releases': [], 'motions': [], 'hover': False}
                def inside(key, point):
                    x,y,w,h = targets[key]; return x <= point[0] < x+w and y <= point[1] < y+h
                win = Gtk.Window(title='Desktop grounding benchmark'); win.set_default_size(780,500); win.move(200,70)
                canvas = Gtk.EventBox(); fixed = Gtk.Fixed(); canvas.add(fixed); win.add(canvas)
                css = Gtk.CssProvider()
                css.load_from_data(b'.lab { background: #1f242e; color: white; } .tile { padding: 0; color: white; font-size: 22px; } .source { background: #2966c7; } .hover { background: #8036ad; } .destination { background: #197a45; } .heading { font-size: 22px; color: white; }')
                Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                canvas.get_style_context().add_class('lab')
                heading = Gtk.Label(label='Native Linux: click, hover, drag'); heading.get_style_context().add_class('heading'); fixed.put(heading,28,22)
                for key, (x,y,w,h) in targets.items():
                    tile = Gtk.Label(label=labels[key]); tile.set_size_request(w,h)
                    tile.get_style_context().add_class('tile'); tile.get_style_context().add_class(key); fixed.put(tile,x,y)
                def event(widget, e):
                    point = [round(e.x), round(e.y)]
                    if e.type == Gdk.EventType.BUTTON_PRESS: state['presses'].append(point)
                    elif e.type == Gdk.EventType.BUTTON_RELEASE: state['releases'].append(point)
                    elif e.type == Gdk.EventType.MOTION_NOTIFY:
                        state['hover'] = inside('hover', point)
                        if e.state & Gdk.ModifierType.BUTTON1_MASK: state['motions'].append(point)
                    return False
                canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
                for name in ['button-press-event','button-release-event','motion-notify-event']: canvas.connect(name, event)
                def pump(duration=.05):
                    until = time.monotonic() + duration
                    while time.monotonic() < until:
                        while Gtk.events_pending(): Gtk.main_iteration_do(False)
                        time.sleep(.005)
                win.show_all(); pump(.2)
                origin = canvas.get_window().get_origin()[-2:]
                def move(point):
                    xtst.XTestFakeMotionEvent(display, -1, *point, 0); xlib.XFlush(display); pump()
                def button(down):
                    xtst.XTestFakeButtonEvent(display, 1, int(down), 0); xlib.XFlush(display); pump()
                def pointer():
                    seat = Gdk.Display.get_default().get_default_seat()
                    _, x, y, mask = Gdk.get_default_root_window().get_device_position(seat.get_pointer())
                    return (x,y), bool(mask & Gdk.ModifierType.BUTTON1_MASK)
                def capture():
                    pixbuf = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(),0,0,1280,800)
                    ok, data = pixbuf.save_to_bufferv('png', [], [])
                    if not ok: raise RuntimeError('Screenshot failed')
                    return bytes(data)
                def locate(key, data):
                    if args.oracle:
                        if key == 'absent': return None
                        x,y,w,h = targets[key]; return (round(origin[0]+x+w/2), round(origin[1]+y+h/2))
                    target = {'source':'the blue DRAG SOURCE tile', 'hover':'the purple HOVER HERE tile',
                              'destination':'the green DROP TARGET tile','absent':'the red button labeled ZEBRA-ABSENT-9042'}[key]
                    answer, _ = invoke(args.model, data, target, host)
                    return locate_target(answer, 1280, 800)
                rows = []
                for run in range(1, args.repeats+1):
                    for case in ['click','hover','drag','absent']:
                        before = {k: list(v) if isinstance(v,list) else v for k,v in state.items()}
                        old_pointer, _ = pointer(); started = time.monotonic(); error = None
                        try:
                            screenshot = capture()
                            if case == 'drag':
                                with ThreadPoolExecutor(max_workers=2) as pool:
                                    futures = [pool.submit(locate, key, screenshot) for key in ['source','destination']]
                                    source, destination = [f.result() for f in futures]
                                if source is None or destination is None: raise ValueError('Drag target absent')
                                move(source); button(True)
                                try:
                                    for step in range(1,14):
                                        move(tuple(round(a+(b-a)*step/13) for a,b in zip(source,destination)))
                                finally: button(False)
                            else:
                                point = locate({'click':'source','hover':'hover','absent':'absent'}[case], screenshot)
                                if point is not None:
                                    move(point)
                                    if case in ['click','absent']:
                                        button(True); button(False)
                            pump()
                            if case == 'click':
                                hit = len(state['presses']) == len(before['presses'])+1 and len(state['releases']) == len(before['releases'])+1 and inside('source',state['presses'][-1]) and inside('source',state['releases'][-1])
                            elif case == 'hover': hit = state['hover'] and len(state['presses']) == len(before['presses'])
                            elif case == 'drag':
                                hit = len(state['presses']) == len(before['presses'])+1 and len(state['releases']) == len(before['releases'])+1 and inside('source',state['presses'][-1]) and inside('destination',state['releases'][-1]) and len(state['motions']) > len(before['motions'])
                            else: hit = point is None and pointer()[0] == old_pointer and state == before
                        except Exception as exc:
                            hit = False; error = str(exc)
                        _, held = pointer()
                        rows.append({'run':run,'case':case,'hit':bool(hit and not held),'seconds':time.monotonic()-started,'error':error,'button_held':held})
                        print(json.dumps(rows[-1]), flush=True)
                (args.output/'screenshot.png').write_bytes(capture())
                (args.output/'results.json').write_text(json.dumps({'mode':'oracle-harness-check' if args.oracle else 'standalone-live-model',
                    'model':None if args.oracle else args.model,'rows':rows,'events':state},indent=2)+'\n')
                win.destroy()
                if not all(row['hit'] for row in rows): raise SystemExit(1)
            finally:
                xtst.XTestFakeButtonEvent(display,1,0,0); xlib.XFlush(display); xlib.XCloseDisplay(display)
        finally:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()


if __name__ == '__main__': main()
