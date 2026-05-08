# ex:ts=4:et:

import gi
import os.path
import subprocess
import warnings
from typing import Any, IO, List, Optional, cast

from . import diff
from .gutterrenderer import GutterRenderer

gi.require_version('Gedit', '3.0')
gi.require_version('Gtk', '3.0')

from gi.repository import GObject, Gedit, GLib, GtkSource, Gtk, Pango, PeasGtk, Gio  # noqa


class JSBeautifyViewActivatable(GObject.Object, Gedit.ViewActivatable):
    view = GObject.Property(type=Gedit.View)
    
    def __init__(self) -> None:
        super().__init__()
        
        self.context_data: diff.Diff = []
        self.update_timeout = 0
        self.parse_signal = 0
        self.connected = False
        self.location: Optional[Gio.File] = None
    
    def do_activate(self) -> None:
        self.gutter_renderer = GutterRenderer(self)
        self.gutter = self.view.get_gutter(Gtk.TextWindowType.LEFT)
        
        self.view_signals: List[int] = [
            self.view.connect('notify::buffer', self.on_notify_buffer),
            self.view.connect('notify::show-right-margin', self.update),
            self.view.connect('notify::right-margin-position', self.update),
        ]
        
        self.buffer: Optional[GtkSource.Buffer] = None
        self.on_notify_buffer(self.view)
    
    def do_deactivate(self) -> None:
        if self.update_timeout != 0:
            GLib.source_remove(self.update_timeout)
        if self.parse_signal != 0:
            GLib.source_remove(self.parse_signal)
            self.parse_signal = 0
        
        self.disconnect_buffer()
        self.buffer = None
        
        self.disconnect_view()
        self.gutter.remove(self.gutter_renderer)
    
    def disconnect_signals(self, obj: GObject.Object, signals: List[int]) -> None:
        for sid in signals:
            obj.disconnect(sid)
        
        signals[:] = []
    
    def disconnect_buffer(self) -> None:
        if self.buffer:
            self.disconnect_signals(self.buffer, self.buffer_signals)
    
    def disconnect_view(self) -> None:
        self.disconnect_signals(self.view, self.view_signals)
    
    def on_notify_buffer(self, view: Any, gspec: Any = None) -> None:
        if self.update_timeout != 0:
            GLib.source_remove(self.update_timeout)
        if self.parse_signal != 0:
            GLib.source_remove(self.parse_signal)
            self.parse_signal = 0
        
        if self.buffer:
            self.disconnect_buffer()
        
        self.buffer = view.get_buffer()
        
        if not self.buffer:
            raise Exception("self.buffer is None")
        
        # The changed signal is connected to in _update_location().
        self.buffer_signals: List[int] = [
            self.buffer.connect('saved', self._update_location),
            self.buffer.connect('loaded', self._update_location),
            self.buffer.connect('notify::language', self._update_location),
        ]
        self._update_location()
    
    def should_check(self) -> bool:
        if not self.buffer:
            return False
        
        lang = self.buffer.get_language()
        if not lang:
            return False
        
        return lang.get_id() == "js"
    
    def _update_location(self, *unused: Any) -> None:
        if not self.buffer:
            return
        
        self.location = cast(Gedit.Document, self.buffer).get_file().get_location()
        
        if not self.should_check():
            self.disconnect_gutter()
            return
        
        self.connect_gutter()
        self.update()
    
    def disconnect_gutter(self) -> None:
        if not self.connected:
            return
        
        self.gutter.remove(self.gutter_renderer)
        if self.buffer:
            self.buffer.disconnect(self.buffer_signals.pop())
        self.connected = False
    
    def connect_gutter(self) -> None:
        if self.connected:
            return
        
        self.gutter.insert(self.gutter_renderer, 60)
        if self.buffer:
            self.buffer_signals.append(self.buffer.connect('changed', self.update))
        self.connected = True
    
    def update(self, *unused: Any) -> None:
        if not self.connected:
            return
        
        # We don't let the delay accumulate
        if self.update_timeout != 0:
            return
        if self.parse_signal != 0:
            GLib.source_remove(self.parse_signal)
            self.parse_signal = 0
        
        # Do the initial diff without a delay
        if not self.context_data:
            self.on_update_timeout()
        else:
            n_lines: int = self.buffer.get_line_count()
            delay = min(10000, 200 * (n_lines // 2000 + 1))
            
            self.update_timeout = GLib.timeout_add(delay, self.on_update_timeout)
    
    def find_user_config(self) -> Optional[str]:
        path = os.path.expanduser("~/.jsbeautifyrc")
        if os.path.isfile(path):
            return path
        else:
            return None
    
    def find_config(self) -> Optional[str]:
        if not self.location:
            return self.find_user_config()
        
        if not self.location.has_parent():
            return self.find_user_config()
        
        folder: Gio.File = self.location
        while folder.has_parent():
            folder = cast(Gio.File, folder.get_parent())
            
            child = folder.get_child(".jsbeautifyrc")
            if child.query_exists():
                return child.get_path()
        
        return self.find_user_config()
    
    def on_update_timeout(self) -> None:
        self.update_timeout = 0
        if self.parse_signal != 0:
            GLib.source_remove(self.parse_signal)
            self.parse_signal = 0
        
        if not self.buffer:
            self.context_data = []
            return
        
        text: str = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        
        args = ["js-beautify"]
        
        config = self.find_config()
        if config:
            args.append("--config")
            args.append(config)
        
        if self.view.get_property("show-right-margin"):
            pos = self.view.get_property("right-margin-position")
            args.append("-w")
            args.append(str(pos))
        
        if self.location:
            cwd = cast(Gio.File, self.location.get_parent()).get_path()
        else:
            cwd = os.path.expanduser("~")
        
        args.append("-")
        
        try:
            proc = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                universal_newlines=True,
            )
        except FileNotFoundError as e:
            warnings.warn("js-beautify could not be found in $PATH: " + str(e))
            return
        
        with cast(IO[str], proc.stdin) as stdin:
            stdin.write(text)
            stdin.close()
        
        data = ""
        
        def on_read(stdout: IO[str], flags: GLib.IOCondition, proc: subprocess.Popen) -> bool:
            nonlocal data
            
            data += stdout.read(4096)
            if not (flags & GLib.IO_HUP):
                return True
            
            try:
                returncode = proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                return True
            
            data += stdout.read()
            
            if returncode:
                warnings.warn(f"js-beautify exited {returncode}")
                self.parse_signal = 0
                return False
            
            self.handle_result(text, data)
            self.parse_signal = 0
            return False
        
        self.parse_signal = GLib.io_add_watch(proc.stdout, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_read, proc)
    
    def handle_result(self, raw: str, formatted: str) -> None:
        lines_a = raw.splitlines(keepends=True)
        lines_b = formatted.splitlines(keepends=True)
        context_data = diff.generate_diff(lines_a, lines_b)
        self.context_data = context_data
        self.gutter_renderer.update()
