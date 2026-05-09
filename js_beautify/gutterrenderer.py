# ex:ts=4:et:

import os.path
import re
from gi.repository import Gdk, GLib, Gtk, GtkSource

from . import diff


class GutterRenderer(GtkSource.GutterRenderer):
    def __init__(self, view) -> None:
        GtkSource.GutterRenderer.__init__(self)
        
        self.view = view
        
        self.set_size(8)
        # self.set_padding(3, 0)
        
        self.file_context = {}
        
        self.connect("query-activatable", self.query_activate)
        self.connect("activate", self.on_activate)
    
    def query_activate(self, cr: GtkSource.GutterRenderer, it: Gtk.TextIter, r: Gdk.Rectangle, ev: Gdk.Event) -> bool:
        if not self.view.context_data:
            return False
        
        line = it.get_line()
        
        for block in self.view.context_data:
            if not isinstance(block, diff.DiffBlock):
                continue
            if block.start_a <= line and line < block.end_a:
                return True
        else:
            return False
    
    def on_activate(self, cr: GtkSource.GutterRenderer, it: Gtk.TextIter, r: Gdk.Rectangle, ev: Gdk.Event) -> None:
        if not self.view.context_data:
            return
        
        line = it.get_line()
        
        for block in self.view.context_data:
            if not isinstance(block, diff.DiffBlock):
                continue
            if block.start_a <= line and line < block.end_a:
                break
        else:
            return
        
        removed = "".join(block.removed)
        added = "".join(block.added)
        
        len_prefix = len(os.path.commonprefix((removed, added)))
        len_suffix = len(os.path.commonprefix((removed[len_prefix:][::-1], added[len_prefix:][::-1])))
        
        edit_start = self.view.buffer.get_iter_at_line_offset(block.start_a, 0)
        edit_start.forward_chars(len_prefix)
        edit_end = edit_start.copy()
        edit_end.forward_chars(len(removed) - len_prefix - len_suffix)
        
        mark_start = self.view.buffer.create_mark(None, edit_start, True)
        mark_end = self.view.buffer.create_mark(None, edit_end, False)
        
        self.view.buffer.begin_user_action()
        
        to_insert = added[len_prefix:len(added) - len_suffix]
        self.view.buffer.delete(edit_start, edit_end)
        self.view.buffer.insert(edit_start, to_insert)
        
        edit_start = self.view.buffer.get_iter_at_mark(mark_start)
        edit_end = self.view.buffer.get_iter_at_mark(mark_end)
        self.view.buffer.select_range(edit_end, edit_start)
        
        self.view.buffer.delete_mark(mark_start)
        self.view.buffer.delete_mark(mark_end)
        
        self.view.buffer.end_user_action()
    
    def do_draw(self, cr, bg_area, cell_area, start, end, state):
        GtkSource.GutterRenderer.do_draw(self, cr, bg_area, cell_area, start, end, state)
        
        if not self.view.context_data:
            return
        
        line = start.get_line()
        
        for block in self.view.context_data:
            if not isinstance(block, diff.DiffBlock):
                continue
            if block.start_a <= line and line < block.end_a:
                break
        else:
            return False
        
        removed = "".join(block.removed)
        added = "".join(block.added)
        
        blocks = diff.generate_diff(removed, added)
        if all(not blk.added for blk in blocks if isinstance(blk, diff.DiffBlock)):
            # every chunk removes characters
            color = "#FF0000"
        elif all(not blk.removed for blk in blocks if isinstance(blk, diff.DiffBlock)):
            # every chunk adds characters
            color = "#00FF00"
        else:
            color = "orange"
        
        background = Gdk.RGBA()
        background.parse(color)
        Gdk.cairo_set_source_rgba(cr, background)
        cr.rectangle(cell_area.x, cell_area.y, cell_area.width, cell_area.height)
        cr.fill()
    
    def do_query_tooltip(self, it, area, x, y, tooltip):
        if not self.view.context_data:
            return False
        
        line = it.get_line()
        for block in self.view.context_data:
            if not isinstance(block, diff.DiffBlock):
                continue
            if block.start_a <= line and line < block.end_a:
                break
        else:
            return False
        
        removed = "".join(block.removed)
        added = "".join(block.added)
        
        blocks = diff.generate_diff(removed, added)
        text = ""
        for block in blocks:
            if isinstance(block, diff.DiffBlock):
                if block.removed:
                    text += '<span bgcolor="#FF0000" bgalpha="50%">{}</span>'.format(self.escape_pango(block.removed))
                if block.added:
                    text += '<span bgcolor="#00FF00" bgalpha="50%">{}</span>'.format(self.escape_pango(block.added))
            if isinstance(block, diff.MatchBlock):
                if block.lines:
                    text += self.escape_pango(block.lines)
        
        tooltip.set_markup(f'<span font="monospace">{text}</span>')
        return True
    
    @staticmethod
    def escape_pango(string: str) -> str:
        string = "".join(string)
        
        result = ""
        for segment in re.split("( +)", string):
            if segment == "":
                continue
            if segment[0] == " ":
                result += '<span fgalpha="50%">{}</span>'.format(segment.replace(" ", "·"))
            else:
                result += GLib.markup_escape_text(segment)
        
        return result
    
    def update(self):
        self.queue_draw()
