# ex:ts=4:et:

import gi
import os.path
import subprocess
import threading
import warnings
from typing import Any, Callable, List, Optional, cast

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
        self.update_thread: Optional[threading.Thread] = None
        self.active = False
        self.dirty = False
        self.connected = False
        self.location: Optional[Gio.File] = None
    
    def do_activate(self) -> None:
        self.active = True
        self.dirty = True
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
        self.active = False
        self.dirty = False
        if self.update_timeout != 0:
            GLib.source_remove(self.update_timeout)
        
        self.cleanup_context_data()
        
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
    
    def cleanup_context_data(self) -> None:
        "this could be used to remove marks from the active buffer if needed"
        # for msg in self.context_data:
        #     msg.cleanup()
        self.context_data = []
    
    def on_notify_buffer(self, view: Any, gspec: Any = None) -> None:
        if self.update_timeout != 0:
            GLib.source_remove(self.update_timeout)
        
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
        
        if not self.buffer:
            return
        
        self.dirty = True
        self.update_timeout = GLib.timeout_add(1000, self.on_update_timeout)
    
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
        
        if not (self.dirty and self.active):
            return
        
        if not self.buffer:
            self.cleanup_context_data()
            return
        
        if self.update_thread:
            return
        
        text: str = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        config = self.find_config()
        if self.view.get_property("show-right-margin"):
            line_width = self.view.get_property("right-margin-position")
        else:
            line_width = None
        
        if self.location:
            cwd = cast(Gio.File, self.location.get_parent()).get_path()
        else:
            cwd = os.path.expanduser("~")
        
        self.dirty = False
        args = (text, config, line_width, cwd, self.handle_result)
        self.update_thread = threading.Thread(target=get_context_data, args=args)
        self.update_thread.start()
    
    def handle_result(self, context_data: Optional[diff.Diff]) -> None:
        self.update_thread = None
        
        if not self.active:
            return
        
        self.cleanup_context_data()
        if context_data:
            self.context_data = context_data
        self.gutter_renderer.update()
        
        if self.dirty:
            self.update_timeout = GLib.timeout_add(1000, self.on_update_timeout)


def get_context_data(
    text: str, config: Optional[str], line_width: Optional[int], cwd: str, callback: Callable[[diff.Diff], Any],
) -> None:
    "run in background thread"
    
    args = ["js-beautify"]
    
    if config:
        args.append("--config")
        args.append(config)
    
    if line_width is not None:
        args.append("-w")
        args.append(str(line_width))
    
    args.append("-")
    
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            input=text,
            stdout=subprocess.PIPE,
            universal_newlines=True,
            check=True,
        )
    except FileNotFoundError as e:
        warnings.warn("js-beautify could not be found in $PATH: " + str(e))
        GLib.idle_add(callback, None)
        return
    except subprocess.CalledProcessError as e:
        warnings.warn(f"js-beautify exited {e.returncode}")
        GLib.idle_add(callback, None)
        return
    
    formatted: str = proc.stdout
    
    lines_a = text.splitlines(keepends=True)
    lines_b = formatted.splitlines(keepends=True)
    context_data = diff.generate_diff(lines_a, lines_b)
    
    GLib.idle_add(callback, context_data)
