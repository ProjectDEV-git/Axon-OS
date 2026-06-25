"""System-Wide AI Search — unified search across files, apps, settings, and web.

Provides a single D-Bus interface (org.axonos.GlobalSearch) that fans out
queries to the existing Search, Context, and Brain services, then merges
and ranks results.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axon_logger import configure_app_logger

sys.path.insert(0, str(Path(__file__).resolve().parent))
log = configure_app_logger("axon-global-search", level=__import__("logging").INFO)


class GlobalSearchService(dbus.service.Object):
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        try:
            self.bus_name = dbus.service.BusName("org.axonos.GlobalSearch", bus=self.session_bus)
        except dbus.exceptions.NameExistsException:
            log.error("org.axonos.GlobalSearch service is already running.")
            sys.exit(1)
        dbus.service.Object.__init__(self, self.session_bus, "/org/axonos/GlobalSearch")

        self._brain = None
        self._search = None
        self._context = None
        self._lock = threading.Lock()
        self._recent_queries: list[dict] = []
        log.info("GlobalSearch registered at /org/axonos/GlobalSearch")

    # ------------------------------------------------------------------
    # Lazy D-Bus proxy helpers
    # ------------------------------------------------------------------

    def _get_proxy(self, bus_name, obj_path, iface):
        try:
            obj = self.session_bus.get_object(bus_name, obj_path)
            return dbus.Interface(obj, iface)
        except dbus.exceptions.DBusException:
            return None

    def _get_brain(self):
        if self._brain is None:
            try:
                obj = self.session_bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
                self._brain = dbus.Interface(obj, "org.axonos.Brain")
            except dbus.exceptions.DBusException:
                return None
        return self._brain

    def _get_search(self):
        if self._search is None:
            try:
                obj = self.session_bus.get_object("org.axonos.Search", "/org/axonos/Search")
                self._search = dbus.Interface(obj, "org.axonos.Search")
            except dbus.exceptions.DBusException:
                return None
        return self._search

    def _get_context(self):
        if self._context is None:
            try:
                obj = self.session_bus.get_object("org.axonos.Context", "/org/axonos/Context")
                self._context = dbus.Interface(obj, "org.axonos.Context")
            except dbus.exceptions.DBusException:
                return None
        return self._context

    # ------------------------------------------------------------------
    # D-Bus API
    # ------------------------------------------------------------------

    @dbus.service.method("org.axonos.GlobalSearch", in_signature="s", out_signature="s")
    def Search(self, query):
        """Unified search across all sources. Returns JSON array of results."""
        if not query or not isinstance(query, str) or len(query) > 500:
            return json.dumps([])

        results = []
        results_lock = threading.Lock()
        threads = []

        def _search_files():
            svc = self._get_search()
            if svc:
                try:
                    raw = svc.Search(query)
                    items = json.loads(raw) if raw else []
                    for item in items:
                        with results_lock:
                            results.append(
                                {
                                    "type": "file",
                                    "title": item.get("path", "").split("/")[-1],
                                    "subtitle": item.get("path", ""),
                                    "score": item.get("score", 0),
                                    "source": "search",
                                }
                            )
                except Exception:
                    pass

        def _search_apps():
            try:
                apps_dir = Path("/usr/share/applications")
                if not apps_dir.exists():
                    apps_dir = Path.home() / ".local" / "share" / "applications"
                if apps_dir.exists():
                    for desktop in apps_dir.glob("*.desktop"):
                        content = desktop.read_text(errors="ignore")
                        name = ""
                        for line in content.splitlines():
                            if line.startswith("Name="):
                                name = line.split("=", 1)[1].strip()
                                break
                        if query.lower() in name.lower() or query.lower() in desktop.stem.lower():
                            with results_lock:
                                results.append(
                                    {
                                        "type": "app",
                                        "title": name or desktop.stem,
                                        "subtitle": "Application",
                                        "score": 0.8,
                                        "source": "desktop",
                                    }
                                )
            except Exception:
                pass

        def _search_settings():
            try:
                schemas = subprocess.run(
                    ["gsettings", "list-schemas"], capture_output=True, text=True, timeout=3
                )
                for schema in schemas.stdout.strip().splitlines():
                    if query.lower() in schema.lower():
                        with results_lock:
                            results.append(
                                {
                                    "type": "setting",
                                    "title": schema,
                                    "subtitle": "GNOME Setting",
                                    "score": 0.5,
                                    "source": "gsettings",
                                }
                            )
            except Exception:
                pass

        t1 = threading.Thread(target=_search_files, daemon=True)
        t2 = threading.Thread(target=_search_apps, daemon=True)
        t3 = threading.Thread(target=_search_settings, daemon=True)
        threads = [t1, t2, t3]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        top_results = results[:20]

        with self._lock:
            self._recent_queries.append({"query": query, "count": len(top_results)})
            if len(self._recent_queries) > 100:
                self._recent_queries = self._recent_queries[-100:]

        return json.dumps(top_results)

    @dbus.service.method("org.axonos.GlobalSearch", in_signature="s", out_signature="s")
    def AIAnswer(self, query):
        """Use Brain to answer a question with context from search results."""
        if not query or not isinstance(query, str):
            return json.dumps({"error": "empty query"})

        search_results = json.loads(self.Search(query))
        context_parts = []
        for r in search_results[:5]:
            context_parts.append(f"[{r['type']}] {r['title']}: {r.get('subtitle', '')}")
        context = "\n".join(context_parts)

        brain = self._get_brain()
        if brain:
            try:
                prompt = f"Based on these search results, answer the question: {query}"
                if context:
                    prompt = f"Search results:\n{context}\n\nQuestion: {query}"
                return brain.Generate(prompt, context, "", False)
            except Exception as e:
                return json.dumps({"error": str(e)})

        return json.dumps({"error": "Brain service not available"})

    @dbus.service.method("org.axonos.GlobalSearch", in_signature="", out_signature="s")
    def GetRecentQueries(self):
        """Return recent search queries for autocomplete."""
        with self._lock:
            return json.dumps(self._recent_queries[-20:])


if __name__ == "__main__":
    loop = GLib.MainLoop()
    service = GlobalSearchService()
    try:
        loop.run()
    except KeyboardInterrupt:
        loop.quit()
