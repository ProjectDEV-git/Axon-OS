#!/usr/bin/env python3
"""Axon Files — Window and layout components."""

import os
import subprocess
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Gdk, Gio

from file_indexer import FileIndexer, format_size, format_timestamp

def load_css():
    """Loads application CSS styling."""
    css_path = Path(__file__).parent / "main.css"
    if css_path.exists():
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css_path))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, name, icon_name, path_val):
        super().__init__()
        self.name = name
        self.path_val = path_val
        
        self.get_style_context().add_class("sidebar-item")
        
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(18)
        box.append(icon)
        
        lbl = Gtk.Label(label=name)
        lbl.set_halign(Gtk.Align.START)
        box.append(lbl)
        
        self.set_child(box)

class FileRow(Gtk.ListBoxRow):
    def __init__(self, item):
        super().__init__()
        self.file_info = item
        
        self.get_style_context().add_class("file-row")
        
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        
        # Icon based on type
        if item.get('is_dir', False):
            icon_name = "folder-symbolic"
        else:
            ftype = item.get('file_type', '').lower()
            if ftype in ['jpg', 'jpeg', 'png', 'gif', 'svg', 'bmp', 'webp']:
                icon_name = "image-x-generic-symbolic"
            elif ftype in ['mp3', 'ogg', 'wav', 'flac', 'm4a']:
                icon_name = "audio-x-generic-symbolic"
            elif ftype in ['mp4', 'mkv', 'avi', 'mov', 'webm']:
                icon_name = "video-x-generic-symbolic"
            elif ftype in ['py', 'js', 'ts', 'tsx', 'jsx', 'rs', 'c', 'cpp', 'h', 'sh', 'html', 'css', 'toml', 'json', 'yaml', 'yml']:
                icon_name = "text-x-script-symbolic"
            else:
                icon_name = "text-x-generic-symbolic"
                
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(24)
        hbox.append(icon)
        
        # Text details
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_hexpand(True)
        
        name_lbl = Gtk.Label(label=item['file_name'])
        name_lbl.set_halign(Gtk.Align.START)
        name_lbl.get_style_context().add_class("file-name-label")
        name_lbl.set_ellipsize(3) # Pango.EllipsizeMode.END
        vbox.append(name_lbl)
        
        # Show path or snippet
        path_lbl = Gtk.Label(label=item['file_path'])
        path_lbl.set_halign(Gtk.Align.START)
        path_lbl.get_style_context().add_class("file-path-label")
        path_lbl.set_ellipsize(3)
        vbox.append(path_lbl)
        
        hbox.append(vbox)
        
        # Similarity score badge
        sim = item.get('similarity', 0.0)
        if sim > 0:
            sim_pct = int(sim * 100)
            sim_badge = Gtk.Label()
            sim_badge.set_markup(f"<span class='badge-similarity'>AI Match {sim_pct}%</span>")
            sim_badge.get_style_context().add_class("badge-similarity")
            hbox.append(sim_badge)
            
        # File type badge
        if not item.get('is_dir', False) and item.get('file_type'):
            type_badge = Gtk.Label(label=item['file_type'].upper())
            type_badge.get_style_context().add_class("badge-type")
            hbox.append(type_badge)
            
        # File size
        size_str = format_size(item['file_size'])
        size_lbl = Gtk.Label(label=size_str)
        size_lbl.get_style_context().add_class("file-meta-label")
        size_lbl.set_width_chars(10)
        size_lbl.set_xalign(1.0)
        hbox.append(size_lbl)
        
        # Modified date
        date_str = format_timestamp(item['last_modified'])
        date_lbl = Gtk.Label(label=date_str)
        date_lbl.get_style_context().add_class("file-meta-label")
        date_lbl.set_width_chars(18)
        date_lbl.set_xalign(1.0)
        hbox.append(date_lbl)
        
        self.set_child(hbox)

class FilesWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Axon Files")
        self.set_default_size(1000, 680)
        
        self.indexer = FileIndexer()
        self.current_dir = None
        self.search_query = ""
        self.use_semantic = False
        
        # Background worker sync state
        self.sync_thread = None
        
        # Load external stylesheet
        load_css()
        self.get_style_context().add_class("window-bg")
        
        # Top-level Box
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Header bar
        header = Adw.HeaderBar()
        
        # Sync index button + Spinner
        sync_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.spinner = Gtk.Spinner()
        sync_box.append(self.spinner)
        
        self.sync_btn = Gtk.Button(label="Sync Index")
        self.sync_btn.get_style_context().add_class("sync-btn")
        self.sync_btn.connect("clicked", self.on_sync_clicked)
        sync_box.append(self.sync_btn)
        
        header.pack_start(sync_box)
        
        # Search Box in center of HeaderBar
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search files, folders or content...")
        self.search_entry.set_width_chars(32)
        self.search_entry.connect("search-changed", self.on_search_changed)
        search_box.append(self.search_entry)
        
        # AI Semantic Search Toggle
        self.ai_toggle = Gtk.ToggleButton(label="AI Search")
        self.ai_toggle.get_style_context().add_class("ai-toggle-btn")
        self.ai_toggle.connect("toggled", self.on_ai_toggled)
        search_box.append(self.ai_toggle)
        
        header.set_title_widget(search_box)
        
        root_box.append(header)
        
        # Split layout: Sidebar, Main Area, Preview Panel
        layout_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        
        # Left Panel (Sidebar)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.get_style_context().add_class("sidebar")
        sidebar_box.set_size_request(200, -1)
        
        sidebar_title = Gtk.Label(label="NAVIGATION")
        sidebar_title.set_halign(Gtk.Align.START)
        sidebar_title.set_margin_top(16)
        sidebar_title.set_margin_bottom(8)
        sidebar_title.set_margin_start(16)
        sidebar_title.get_style_context().add_class("file-path-label")
        sidebar_box.append(sidebar_title)
        
        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.get_style_context().add_class("sidebar-list")
        self.sidebar_list.connect("row-selected", self.on_sidebar_selected)
        
        # Populate sidebar items
        self.sidebar_list.append(SidebarRow("Search Index", "system-search-symbolic", None))
        self.sidebar_list.append(SidebarRow("Home", "user-home-symbolic", str(Path.home())))
        self.sidebar_list.append(SidebarRow("Documents", "folder-documents-symbolic", str(Path.home() / "Documents")))
        self.sidebar_list.append(SidebarRow("Downloads", "folder-download-symbolic", str(Path.home() / "Downloads")))
        self.sidebar_list.append(SidebarRow("Desktop", "user-desktop-symbolic", str(Path.home() / "Desktop")))
        self.sidebar_list.append(SidebarRow("Pictures", "folder-pictures-symbolic", str(Path.home() / "Pictures")))
        self.sidebar_list.append(SidebarRow("Videos", "folder-videos-symbolic", str(Path.home() / "Videos")))
        self.sidebar_list.append(SidebarRow("Music", "folder-music-symbolic", str(Path.home() / "Music")))
        
        sidebar_box.append(self.sidebar_list)
        layout_box.append(sidebar_box)
        
        # Center Panel (Main Content)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.get_style_context().add_class("main-content")
        main_box.set_hexpand(True)
        
        # Breadcrumbs/Path bar
        self.path_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.path_bar_box.get_style_context().add_class("path-bar")
        main_box.append(self.path_bar_box)
        
        # Scrolled file list
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        
        self.file_list = Gtk.ListBox()
        self.file_list.get_style_context().add_class("file-list")
        self.file_list.connect("row-selected", self.on_file_selected)
        self.file_list.connect("row-activated", self.on_file_activated)
        
        scroll.set_child(self.file_list)
        main_box.append(scroll)
        
        # Bottom status label
        self.status_lbl = Gtk.Label(label="Index state: Loading...")
        self.status_lbl.set_halign(Gtk.Align.START)
        self.status_lbl.get_style_context().add_class("file-path-label")
        self.status_lbl.set_margin_top(8)
        main_box.append(self.status_lbl)
        
        layout_box.append(main_box)
        
        # Right Panel (AI Preview)
        self.preview_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.preview_panel.get_style_context().add_class("preview-panel")
        
        # Placeholder details
        self.preview_placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.preview_placeholder.set_valign(Gtk.Align.CENTER)
        self.preview_placeholder.set_vexpand(True)
        
        ph_icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        ph_icon.set_pixel_size(48)
        ph_icon.set_opacity(0.3)
        self.preview_placeholder.append(ph_icon)
        
        self.ph_lbl = Gtk.Label(label="Select a file to preview AI details")
        self.ph_lbl.set_justify(Gtk.Justification.CENTER)
        self.ph_lbl.get_style_context().add_class("file-meta-label")
        self.preview_placeholder.append(self.ph_lbl)
        
        self.preview_panel.append(self.preview_placeholder)
        
        # Detailed preview box
        self.preview_details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.preview_details.set_visible(False)
        
        self.prev_title = Gtk.Label()
        self.prev_title.get_style_context().add_class("preview-title")
        self.prev_title.set_halign(Gtk.Align.START)
        self.prev_title.set_wrap(True)
        self.prev_title.set_max_width_chars(25)
        self.preview_details.append(self.prev_title)
        
        self.prev_meta = Gtk.Label()
        self.prev_meta.get_style_context().add_class("preview-meta")
        self.prev_meta.set_halign(Gtk.Align.START)
        self.prev_meta.set_wrap(True)
        self.preview_details.append(self.prev_meta)
        
        summary_title = Gtk.Label(label="AI Content Summary:")
        summary_title.set_halign(Gtk.Align.START)
        summary_title.get_style_context().add_class("file-name-label")
        self.preview_details.append(summary_title)
        
        prev_scroll = Gtk.ScrolledWindow()
        prev_scroll.set_size_request(-1, 250)
        prev_scroll.set_vexpand(True)
        
        self.prev_summary = Gtk.TextView()
        self.prev_summary.set_editable(False)
        self.prev_summary.set_wrap_mode(Gtk.WrapMode.WORD)
        self.prev_summary.get_style_context().add_class("preview-summary-box")
        prev_scroll.set_child(self.prev_summary)
        
        self.preview_details.append(prev_scroll)
        
        # Action Open button
        self.open_btn = Gtk.Button(label="Open File")
        self.open_btn.get_style_context().add_class("sync-btn")
        self.open_btn.connect("clicked", self.on_open_clicked)
        self.preview_details.append(self.open_btn)
        
        self.preview_panel.append(self.preview_details)
        layout_box.append(self.preview_panel)
        
        root_box.append(layout_box)
        self.set_content(root_box)
        
        # Select first row in sidebar ("Search Index")
        self.sidebar_list.select_row(self.sidebar_list.get_row_at_index(0))
        self.update_status_label()

    def update_status_label(self):
        """Updates the status line indicating how many files are indexed."""
        try:
            count = self.indexer.get_indexed_count()
            self.status_lbl.set_text(f"Index state: {count} files semantically indexed.")
        except Exception:
            self.status_lbl.set_text("Index state: Database offline.")

    def on_sidebar_selected(self, listbox, row):
        if row is None:
            return
        self.current_dir = row.path_val
        self.refresh_list()
        self.update_path_bar()

    def navigate_to(self, target_path):
        """Helper to navigate via breadcrumbs."""
        self.current_dir = str(target_path)
        self.refresh_list()
        self.update_path_bar()
        
        # Match sidebar selection if it matches one of the root folders
        for idx in range(self.sidebar_list.get_child_visible() or 8):
            row = self.sidebar_list.get_row_at_index(idx)
            if row and row.path_val == self.current_dir:
                self.sidebar_list.select_row(row)
                return
        # If no sidebar row matches, clear selection safely
        self.sidebar_list.select_row(None)

    def update_path_bar(self):
        """Draws breadcrumbs matching current_dir."""
        # Clear path bar
        while child := self.path_bar_box.get_first_child():
            self.path_bar_box.remove(child)
            
        if self.current_dir is None:
            lbl = Gtk.Label(label="Global Search (All Directories)")
            lbl.get_style_context().add_class("file-name-label")
            self.path_bar_box.append(lbl)
            return
            
        path_obj = Path(self.current_dir).expanduser().resolve()
        parts = []
        curr = path_obj
        while curr != curr.parent:
            parts.append(curr)
            curr = curr.parent
        parts.append(curr) # Root folder
        parts.reverse()
        
        first = True
        for p in parts:
            if not first:
                sep = Gtk.Label(label=" › ")
                sep.get_style_context().add_class("file-meta-label")
                self.path_bar_box.append(sep)
            first = False
            
            name = "Home" if p == Path.home() else (p.name if p.name else "/")
            btn = Gtk.Button(label=name)
            btn.get_style_context().add_class("path-btn")
            btn.set_has_frame(False)
            btn.connect("clicked", lambda b, target=p: self.navigate_to(target))
            self.path_bar_box.append(btn)

    def refresh_list(self):
        """Updates the list of files in the center view."""
        # Clear files ListBox
        while child := self.file_list.get_first_child():
            self.file_list.remove(child)
            
        if self.search_query.strip():
            # If search is active, query database
            results = self.indexer.search_local(self.search_query, use_semantic=self.use_semantic)
            
            # Apply folder filtering if browsing directory
            if self.current_dir is not None:
                filtered = []
                curr_path = Path(self.current_dir).expanduser().resolve()
                for r in results:
                    try:
                        r_path = Path(r['file_path']).expanduser().resolve()
                        if curr_path in r_path.parents or curr_path == r_path:
                            filtered.append(r)
                    except Exception:
                        pass
                results = filtered
        else:
            # If browsing folder
            if self.current_dir is not None:
                results = list_directory_contents(self.current_dir, self.indexer)
            else:
                # Global index view (shows 50 latest indexed files)
                results = self.indexer.search_local("")
                
        # Populate ListBox
        for item in results:
            self.file_list.append(FileRow(item))

    def on_search_changed(self, entry):
        self.search_query = entry.get_text()
        self.refresh_list()

    def on_ai_toggled(self, btn):
        self.use_semantic = btn.get_active()
        self.refresh_list()

    def on_file_selected(self, listbox, row):
        if row is None or not hasattr(row, 'file_info'):
            self.show_preview_placeholder()
            return
            
        info = row.file_info
        if info.get('is_dir', False):
            self.show_preview_placeholder(f"Folder: {info['file_name']}")
            return
            
        self.show_preview_details(info)

    def show_preview_placeholder(self, text="Select a file to preview AI details"):
        self.preview_placeholder.set_visible(True)
        self.preview_details.set_visible(False)
        self.ph_lbl.set_text(text)

    def show_preview_details(self, info):
        self.preview_placeholder.set_visible(False)
        self.preview_details.set_visible(True)
        
        self.prev_title.set_text(info['file_name'])
        
        size_str = format_size(info['file_size'])
        date_str = format_timestamp(info['last_modified'])
        meta = f"Type: {info.get('file_type', 'unknown').upper()}\nSize: {size_str}\nModified: {date_str}"
        
        # Add similarity match info to preview metadata
        sim = info.get('similarity', 0.0)
        if sim > 0:
            meta += f"\nAI Relevance: {int(sim*100)}%"
            
        self.prev_meta.set_text(meta)
        
        # Load content summary
        buf = self.prev_summary.get_buffer()
        summary_text = info.get('content_summary', '')
        if not summary_text:
            summary_text = "[No text content or file not yet indexed]"
        buf.set_text(summary_text)
        
        self.selected_file_path = info['file_path']

    def on_file_activated(self, listbox, row):
        if row is None or not hasattr(row, 'file_info'):
            return
        info = row.file_info
        if info.get('is_dir', False):
            self.navigate_to(info['file_path'])
        else:
            self.open_file_path(info['file_path'])

    def on_open_clicked(self, btn):
        if hasattr(self, 'selected_file_path') and self.selected_file_path:
            self.open_file_path(self.selected_file_path)

    def open_file_path(self, file_path):
        try:
            subprocess.Popen(["xdg-open", file_path])
        except Exception as e:
            print(f"Error opening file {file_path}: {e}")

    # --- Sync Index background worker ---
    def on_sync_clicked(self, btn):
        if self.sync_thread and self.sync_thread.is_alive():
            return
            
        self.sync_btn.set_sensitive(False)
        self.spinner.start()
        
        # Get roots to scan
        scan_roots = self.get_default_scan_roots()
        
        self.status_lbl.set_text("Indexing background process started...")
        
        # Setup cancel and progress tracking
        def progress_cb(current_path, indexed, total):
            GLib.idle_add(self.update_sync_progress, current_path, indexed, total)
            
        def done_cb(success, err):
            GLib.idle_add(self.sync_completed, success, err)
            
        # Spawn thread
        self.sync_thread = threading.Thread(
            target=self._run_scan,
            args=(scan_roots, progress_cb, done_cb),
            daemon=True
        )
        self.sync_thread.start()

    def get_default_scan_roots(self):
        """Scans standard folders plus root home directory files."""
        dirs = [
            Path.home() / "Documents",
            Path.home() / "Downloads",
            Path.home() / "Desktop",
            Path.home() / "Pictures",
            Path.home() / "Videos",
            Path.home() / "Music"
        ]
        roots = []
        for d in dirs:
            if d.exists():
                roots.append(str(d))
        # Add files in home root (non-recursive)
        try:
            for entry in Path.home().iterdir():
                if entry.is_file() and not entry.name.startswith('.'):
                    roots.append(str(entry))
        except Exception:
            pass
        return roots

    def _run_scan(self, roots, progress_cb, done_cb):
        try:
            self.indexer.scan_directories(roots, progress_callback=progress_cb)
            done_cb(True, "")
        except Exception as e:
            done_cb(False, str(e))

    def update_sync_progress(self, path, indexed, total):
        self.status_lbl.set_text(f"Indexing: {indexed} of {total} | {Path(path).name}")

    def sync_completed(self, success, err_msg):
        self.spinner.stop()
        self.sync_btn.set_sensitive(True)
        if success:
            self.update_status_label()
            self.refresh_list()
        else:
            self.status_lbl.set_text(f"Indexing failed: {err_msg}")

def list_directory_contents(dir_path, indexer):
    """Utility function to list both folders and database files inside a directory."""
    import sqlite3
    items = []
    try:
        path_obj = Path(dir_path).expanduser().resolve()
        if not path_obj.exists() or not path_obj.is_dir():
            return []
            
        # Select all indexed files residing under this folder
        conn = sqlite3.connect(indexer.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, file_path, file_name, file_type, file_size, last_modified, content_summary
            FROM files
            WHERE file_path LIKE ?
        """, (f"{path_obj}/%",))
        db_files = {row['file_path']: dict(row) for row in cursor.fetchall()}
        conn.close()
        
        for entry in path_obj.iterdir():
            if entry.name.startswith('.'):
                continue
                
            entry_str = str(entry)
            try:
                stat = entry.stat()
                mtime = stat.st_mtime
            except Exception:
                continue
                
            if entry.is_dir():
                items.append({
                    'is_dir': True,
                    'file_path': entry_str,
                    'file_name': entry.name,
                    'file_type': 'Folder',
                    'file_size': None,
                    'last_modified': mtime,
                    'content_summary': '',
                    'similarity': 0.0
                })
            else:
                db_rec = db_files.get(entry_str)
                if db_rec:
                    items.append({
                        'is_dir': False,
                        'file_path': entry_str,
                        'file_name': entry.name,
                        'file_type': db_rec['file_type'],
                        'file_size': db_rec['file_size'],
                        'last_modified': db_rec['last_modified'],
                        'content_summary': db_rec['content_summary'],
                        'similarity': 0.0
                    })
                else:
                    items.append({
                        'is_dir': False,
                        'file_path': entry_str,
                        'file_name': entry.name,
                        'file_type': entry.suffix.lower().lstrip('.'),
                        'file_size': stat.st_size,
                        'last_modified': mtime,
                        'content_summary': 'File is not yet indexed (Sync Index)',
                        'similarity': 0.0
                    })
    except Exception as e:
        print(f"Error listing folder contents: {e}")
        
    dirs = [x for x in items if x['is_dir']]
    files = [x for x in items if not x['is_dir']]
    dirs.sort(key=lambda x: x['file_name'].lower())
    files.sort(key=lambda x: x['file_name'].lower())
    return dirs + files
