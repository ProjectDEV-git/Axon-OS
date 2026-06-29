import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const StartMenuPopup = GObject.registerClass(
class StartMenuPopup extends St.BoxLayout {
    _init(extension, spacesManager, intentBar) {
        super._init({
            style_class: 'axon-startmenu',
            vertical: true,
            reactive: true,
            visible: false,
        });

        this._extension = extension;
        this._spacesManager = spacesManager;
        this._intentBar = intentBar;
        this._appSystem = Shell.AppSystem.get_default();

        this._buildUI();
    }

    _buildUI() {
        // 1. Search Box at the top
        const searchContainer = new St.BoxLayout({
            style_class: 'axon-startmenu-search-container',
            vertical: false,
        });

        this._searchEntry = new St.Entry({
            style_class: 'axon-startmenu-search',
            hint_text: 'Search apps, files or ask AI...',
            can_focus: true,
            x_expand: true,
        });

        this._searchEntry.clutter_text.connect('key-press-event', (actor, event) => {
            const key = event.get_key_symbol();
            if (key === Clutter.KEY_Return || key === Clutter.KEY_KP_Enter) {
                this._onSubmitSearch();
                return Clutter.EVENT_STOP;
            }
            if (key === Clutter.KEY_Escape) {
                this.hide();
                return Clutter.EVENT_STOP;
            }
            if (key === Clutter.KEY_Down) {
                let firstRow = this._appsGrid.get_first_child();
                if (firstRow) {
                    let firstBtn = firstRow.get_first_child();
                    if (firstBtn) firstBtn.grab_key_focus();
                }
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        this._searchEntry.clutter_text.connect('text-changed', () => {
            this._onSearchChanged();
        });

        searchContainer.add_child(this._searchEntry);
        this.add_child(searchContainer);

        // 2. Main content area (split into Pinned Apps and Recent Files)
        const contentBox = new St.BoxLayout({
            style_class: 'axon-startmenu-content',
            vertical: false,
            x_expand: true,
            y_expand: true,
        });
        this.add_child(contentBox);

        // Left column: Pinned/All Apps
        this._appsColumn = new St.BoxLayout({
            style_class: 'axon-startmenu-apps-column',
            vertical: true,
            x_expand: true,
        });
        contentBox.add_child(this._appsColumn);

        const appsTitle = new St.Label({
            style_class: 'axon-startmenu-section-title',
            text: 'Pinned Apps',
        });
        this._appsColumn.add_child(appsTitle);

        this._appsGrid = new St.BoxLayout({
            style_class: 'axon-startmenu-apps-grid',
            vertical: true,
        });
        this._appsColumn.add_child(this._appsGrid);

        // Right column: Recent Files (queried from SQLite)
        this._filesColumn = new St.BoxLayout({
            style_class: 'axon-startmenu-files-column',
            vertical: true,
            width: 280,
        });
        contentBox.add_child(this._filesColumn);

        const filesTitle = new St.Label({
            style_class: 'axon-startmenu-section-title',
            text: 'Recommended Files',
        });
        this._filesColumn.add_child(filesTitle);

        this._filesList = new St.BoxLayout({
            style_class: 'axon-startmenu-files-list',
            vertical: true,
        });
        this._filesColumn.add_child(this._filesList);

        // 3. Bottom Profile & Power Menu
        const footer = new St.BoxLayout({
            style_class: 'axon-startmenu-footer',
            vertical: false,
        });
        this.add_child(footer);

        // User info
        const userBox = new St.BoxLayout({
            style_class: 'axon-startmenu-user',
            vertical: false,
            x_expand: true,
        });
        const userIcon = new St.Icon({
            icon_name: 'avatar-default-symbolic',
            icon_size: 24,
            style_class: 'axon-startmenu-avatar',
        });
        const userName = new St.Label({
            text: GLib.get_real_name() || GLib.get_user_name() || 'Axon User',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'axon-startmenu-username',
        });
        userBox.add_child(userIcon);
        userBox.add_child(userName);
        footer.add_child(userBox);

        // Power actions
        const powerBox = new St.BoxLayout({
            style_class: 'axon-startmenu-power-box',
            vertical: false,
            spacing: 12,
        });

        const lockBtn = new St.Button({
            style_class: 'axon-startmenu-power-btn',
            child: new St.Icon({ icon_name: 'system-lock-screen-symbolic', icon_size: 16 }),
        });
        lockBtn.connect('clicked', () => {
            this.hide();
            Gio.Subprocess.new(['xdg-screensaver', 'lock'], Gio.SubprocessFlags.NONE);
        });

        const restartBtn = new St.Button({
            style_class: 'axon-startmenu-power-btn',
            child: new St.Icon({ icon_name: 'system-reboot-symbolic', icon_size: 16 }),
        });
        restartBtn.connect('clicked', () => {
            this.hide();
            Gio.Subprocess.new(['systemctl', 'reboot'], Gio.SubprocessFlags.NONE);
        });

        const shutdownBtn = new St.Button({
            style_class: 'axon-startmenu-power-btn',
            child: new St.Icon({ icon_name: 'system-shutdown-symbolic', icon_size: 16 }),
        });
        shutdownBtn.connect('clicked', () => {
            this.hide();
            Gio.Subprocess.new(['systemctl', 'poweroff'], Gio.SubprocessFlags.NONE);
        });

        powerBox.add_child(lockBtn);
        powerBox.add_child(restartBtn);
        powerBox.add_child(shutdownBtn);
        footer.add_child(powerBox);

        Main.uiGroup.add_child(this);
    }

    show() {
        if (this.visible) return;
        this.visible = true;

        this._searchEntry.set_text('');
        this._populatePinnedApps();
        this._populateRecentFiles();
        this._reposition();

        this.set_opacity(0);
        this.ease({
            opacity: 255,
            duration: 150,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });

        this._searchEntry.grab_key_focus();

        if (!this._clickDismissId) {
            this._clickDismissId = global.stage.connect('button-press-event', (actor, event) => {
                if (!this.contains(event.get_source())) {
                    this.hide();
                }
                return Clutter.EVENT_PROPAGATE;
            });
        }
    }

    hide() {
        if (!this.visible) return;
        
        if (this._clickDismissId) {
            global.stage.disconnect(this._clickDismissId);
            this._clickDismissId = null;
        }

        this.ease({
            opacity: 0,
            duration: 100,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => {
                this.visible = false;
            },
        });
    }

    toggle() {
        if (this.visible) {
            this.hide();
        } else {
            this.show();
        }
    }

    _reposition() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;

        const [, natW] = this.get_preferred_width(-1);
        const [, natH] = this.get_preferred_height(natW);

        // Position bottom-left, floating slightly above taskbar (height ~48px)
        const x = monitor.x + 20;
        const y = monitor.y + monitor.height - natH - 60;

        this.set_position(x, y);
    }

    _populatePinnedApps() {
        // Clear grid
        this._appsGrid.destroy_all_children();

        const defaultApps = [
            'axon-welcome.desktop',
            'axon-ai-panel.desktop',
            'axon-terminal.desktop',
            'axon-files.desktop',
            'axon-settings.desktop',
            'org.gnome.Epiphany.desktop',
        ];

        let row = new St.BoxLayout({ vertical: false, spacing: 10 });
        this._appsGrid.add_child(row);
        let count = 0;

        defaultApps.forEach(appId => {
            const app = this._appSystem.lookup_app(appId);
            if (!app) return;

            if (count > 0 && count % 3 === 0) {
                row = new St.BoxLayout({ vertical: false, spacing: 10 });
                this._appsGrid.add_child(row);
            }

            const btn = new St.Button({
                style_class: 'axon-startmenu-app-item',
                reactive: true,
                can_focus: true,
                x_expand: true,
            });

            const box = new St.BoxLayout({ vertical: true, x_align: Clutter.ActorAlign.CENTER });
            const icon = app.create_icon_texture(32);
            const label = new St.Label({
                text: app.get_name(),
                style_class: 'axon-startmenu-app-label',
                x_align: Clutter.ActorAlign.CENTER,
            });

            box.add_child(icon);
            box.add_child(label);
            btn.set_child(box);

            btn.connect('clicked', () => {
                this.hide();
                app.activate();
            });

            btn.connect('key-press-event', (actor, event) => {
                const key = event.get_key_symbol();
                const buttons = this._getAppButtons();
                const idx = buttons.indexOf(actor);
                if (idx < 0) return Clutter.EVENT_PROPAGATE;

                if (key === Clutter.KEY_Return || key === Clutter.KEY_KP_Enter) {
                    actor.activate();
                    return Clutter.EVENT_STOP;
                }

                let nextIndex = null;
                if (key === Clutter.KEY_Right || key === Clutter.KEY_Down) {
                    nextIndex = (idx + 1) % buttons.length;
                } else if (key === Clutter.KEY_Left || key === Clutter.KEY_Up) {
                    nextIndex = (idx - 1 + buttons.length) % buttons.length;
                }

                if (nextIndex !== null) {
                    buttons[nextIndex].grab_key_focus();
                    return Clutter.EVENT_STOP;
                }
                return Clutter.EVENT_PROPAGATE;
            });

            row.add_child(btn);
            count++;
        });
    }

    _populateRecentFiles() {
        this._filesList.destroy_all_children();

        try {
            const dbPath = GLib.build_filenamev([GLib.get_home_dir(), '.axon', 'files_index.db']);
            if (!GLib.file_test(dbPath, GLib.FileTest.EXISTS)) {
                this._showNoRecentFiles();
                return;
            }

            // Query SQLite via sqlite3 tool asynchronously
            const query = 'SELECT file_name, file_path FROM files ORDER BY last_modified DESC LIMIT 5;';
            const argv = ['sqlite3', dbPath, query];
            
            const proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
            proc.communicate_utf8_async(null, null, (p, res) => {
                try {
                    let [, stdout, stderr] = p.communicate_utf8_finish(res);
                    if (p.get_successful() && stdout && stdout.length > 0) {
                        const lines = stdout.split('\n');
                        let count = 0;
                        lines.forEach(line => {
                            if (!line.trim()) return;
                            const parts = line.split('|');
                            if (parts.length < 2) return;
                            const name = parts[0];
                            const path = parts[1];

                            const btn = new St.Button({
                                style_class: 'axon-startmenu-file-item',
                                reactive: true,
                                x_expand: true,
                            });
                            const box = new St.BoxLayout({ vertical: false, spacing: 8 });
                            const icon = new St.Icon({
                                icon_name: 'document-open-symbolic',
                                icon_size: 16,
                                style_class: 'axon-startmenu-file-icon',
                            });
                            const label = new St.Label({
                                text: name,
                                y_align: Clutter.ActorAlign.CENTER,
                                style_class: 'axon-startmenu-file-label',
                            });

                            box.add_child(icon);
                            box.add_child(label);
                            btn.set_child(box);

                            btn.connect('clicked', () => {
                                this.hide();
                                Gio.Subprocess.new(['xdg-open', path], Gio.SubprocessFlags.NONE);
                            });

                            this._filesList.add_child(btn);
                            count++;
                        });

                        if (count === 0) {
                            this._showNoRecentFiles();
                        }
                    } else {
                        this._showNoRecentFiles();
                    }
                } catch (err) {
                    console.error('AxonStartMenu: recent files async query error:', err.message);
                    this._showNoRecentFiles();
                }
            });

        } catch (e) {
            console.error('AxonStartMenu: recent files setup error:', e.message);
            this._showNoRecentFiles();
        }
    }

    _showNoRecentFiles() {
        const label = new St.Label({
            text: 'No recent files found.',
            style_class: 'axon-startmenu-no-files',
        });
        this._filesList.add_child(label);
    }

    _onSearchChanged() {
        const text = this._searchEntry.get_text().toLowerCase().trim();
        if (!text) {
            this._populatePinnedApps();
            return;
        }

        // Filter apps
        this._appsGrid.destroy_all_children();
        const apps = this._appSystem.get_installed();
        let count = 0;
        let row = new St.BoxLayout({ vertical: false, spacing: 10 });
        this._appsGrid.add_child(row);

        apps.forEach(app => {
            const name = app.get_name().toLowerCase();
            const desc = (app.get_description() || '').toLowerCase();
            if (name.includes(text) || desc.includes(text)) {
                if (count > 0 && count % 3 === 0) {
                    row = new St.BoxLayout({ vertical: false, spacing: 10 });
                    this._appsGrid.add_child(row);
                }

                const btn = new St.Button({
                    style_class: 'axon-startmenu-app-item',
                    reactive: true,
                    can_focus: true,
                    x_expand: true,
                });

                const box = new St.BoxLayout({ vertical: true, x_align: Clutter.ActorAlign.CENTER });
                const icon = app.create_icon_texture(32);
                const label = new St.Label({
                    text: app.get_name(),
                    style_class: 'axon-startmenu-app-label',
                    x_align: Clutter.ActorAlign.CENTER,
                });

                box.add_child(icon);
                box.add_child(label);
                btn.set_child(box);

                btn.connect('clicked', () => {
                    this.hide();
                    app.activate();
                });

                btn.connect('key-press-event', (actor, event) => {
                    const key = event.get_key_symbol();
                    const buttons = this._getAppButtons();
                    const idx = buttons.indexOf(actor);
                    if (idx < 0) return Clutter.EVENT_PROPAGATE;

                    if (key === Clutter.KEY_Return || key === Clutter.KEY_KP_Enter) {
                        actor.activate();
                        return Clutter.EVENT_STOP;
                    }

                    let nextIndex = null;
                    if (key === Clutter.KEY_Right || key === Clutter.KEY_Down) {
                        nextIndex = (idx + 1) % buttons.length;
                    } else if (key === Clutter.KEY_Left || key === Clutter.KEY_Up) {
                        nextIndex = (idx - 1 + buttons.length) % buttons.length;
                    }

                    if (nextIndex !== null) {
                        buttons[nextIndex].grab_key_focus();
                        return Clutter.EVENT_STOP;
                    }
                    return Clutter.EVENT_PROPAGATE;
                });

                row.add_child(btn);
                count++;
            }
        });
    }

    _getAppButtons() {
        const buttons = [];
        for (const row of this._appsGrid.get_children()) {
            for (const child of row.get_children()) {
                if (child instanceof St.Button) {
                    buttons.push(child);
                }
            }
        }
        return buttons;
    }

    _onSubmitSearch() {
        const text = this._searchEntry.get_text().trim();
        if (!text) return;

        // If it matches an app name, run it
        const apps = this._appSystem.get_installed();
        let matchedApp = null;
        for (const app of apps) {
            if (app.get_name().toLowerCase() === text.toLowerCase()) {
                matchedApp = app;
                break;
            }
        }

        if (matchedApp) {
            this.hide();
            matchedApp.activate();
        } else {
            // No app match, trigger AI Intent Bar
            this.hide();
            if (this._intentBar) {
                this._intentBar.show();
                this._intentBar._entry.set_text(text);
                this._intentBar._onSubmit();
            }
        }
    }
});

export default StartMenuPopup;
