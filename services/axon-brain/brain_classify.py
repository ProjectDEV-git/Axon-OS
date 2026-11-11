"""brain_classify.py — D-Bus API for window routing, intent classification and embeddings.

Mixed into ``BrainService`` (see ``brain_service.py``); methods rely on its state
(``self.store``, ``self.config``, ``self.providers`` ...) set up in ``_setup``.
"""

import json
import logging

import dbus
import dbus.service
from brain_sanitize import _sanitize_context
from constants import OLLAMA_BASE_URL
from service_utils import rate_limited
from window_routing import (
    DEFAULT_SPACE,
    KNOWN_APP_CONFIDENCE,
    classify_known_app,
    score_model_answer,
)

logger = logging.getLogger("axon-brain")


class ClassifyMixin(dbus.service.Object):
    """D-Bus API for window routing, intent classification and embeddings."""

    _CLASSIFY_WINDOW_PROMPT = (
        "You are a workspace routing assistant for Axon OS. Classify this window into one "
        "of these 9 workspace spaces:\n"
        "Code, Web, Chat, Files, Media, Work, Personal, Terminal, Notes.\n"
        "Respond with ONLY the exact space name (one word, capitalised, e.g., 'Code' or "
        "'Web'). No other text or markdown."
    )

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def ClassifyWindowDetailed(self, title, wm_class):
        """Classify a window and say how sure the answer is.

        Returns JSON ``{"space": str, "confidence": float, "source": str}``
        where source is ``rule`` (well-known app), ``model`` or ``none``.
        The Shell asks the user instead of moving the window when the
        confidence is low.
        """
        known = classify_known_app(str(wm_class))
        if known is not None:
            return json.dumps(
                {"space": known, "confidence": KNOWN_APP_CONFIDENCE, "source": "rule"}
            )
        model = self.router.get_model_for_classify_window()
        try:
            raw = self._complete(
                model,
                self._CLASSIFY_WINDOW_PROMPT,
                f"App: {_sanitize_context(wm_class)}\nWindow Title: {_sanitize_context(title)}",
                temperature=0.1,
                timeout=5.0,
            )
        except Exception as e:
            logger.debug("Window classification failed: %s", e)
            return json.dumps({"space": DEFAULT_SPACE, "confidence": 0.0, "source": "none"})
        space, confidence = score_model_answer(raw)
        return json.dumps({"space": space, "confidence": confidence, "source": "model"})

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def ClassifyWindow(self, title, wm_class):
        """Classifies a newly opened window title/class into one of the 9 spaces."""
        model = self.router.get_model_for_classify_window()
        prompt = f"App: {_sanitize_context(wm_class)}\nWindow Title: {_sanitize_context(title)}"
        system_prompt = (
            "You are a workspace routing assistant for Axon OS. Classify this window into one of these 9 workspace spaces:\n"
            "Code, Web, Chat, Files, Media, Work, Personal, Terminal, Notes.\n"
            "Respond with ONLY the exact space name (one word, capitalised, e.g., 'Code' or 'Web'). No other text or markdown."
        )
        try:
            raw = self._complete(model, system_prompt, prompt, temperature=0.1, timeout=5.0)
            result = raw.strip().replace('"', "").replace("'", "")
            valid_spaces = [
                "Code",
                "Web",
                "Chat",
                "Files",
                "Media",
                "Work",
                "Personal",
                "Terminal",
                "Notes",
            ]
            for s in valid_spaces:
                if s.lower() in result.lower():
                    return s
        except Exception as e:
            logger.debug("Window classification failed: %s", e)
        return "Default"

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    @rate_limited(rate=100, window_seconds=60)
    def ClassifyIntent(self, text):
        """Spotlight-style classification of workspace intents using the Speed model."""
        model = self.router.get_model_for_intent()
        system_prompt = (
            "You are a command classifier for Axon OS. Classify the user query into one of these types:\n"
            "1. Run command: {'action': 'run_command', 'command': '<shell command>'}\n"
            "2. Open application: {'action': 'open_app', 'app': '<executable>'}\n"
            "3. Default answer: Just respond in plain text.\n"
            "Respond ONLY with valid JSON if action, otherwise plain text. Keep it brief."
        )
        try:
            result = self._complete(model, system_prompt, text, temperature=0.1, timeout=10.0)
        except Exception as e:
            logger.debug("ClassifyIntent failed: %s", e)
            return '{"action": "error", "message": "AI classification unavailable"}'
        result = result.strip()
        if result.startswith("{"):
            try:
                parsed = json.loads(result)
                if (
                    isinstance(parsed, dict)
                    and parsed.get("action") == "run_command"
                    and isinstance(parsed.get("command"), str)
                ):
                    return json.dumps({"action": "run_command", "command": parsed["command"]})
                if (
                    isinstance(parsed, dict)
                    and parsed.get("action") == "open_app"
                    and isinstance(parsed.get("app"), str)
                ):
                    return json.dumps({"action": "open_app", "app": parsed["app"]})
            except json.JSONDecodeError:
                pass
            return text
        return result

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def GetEmbeddings(self, prompt, model):
        """Generates embedding vector for a given prompt using Ollama."""
        if not model:
            model = self.config.get("embedding_model", "nomic-embed-text")
        try:
            # Try newer /api/embed endpoint first
            payload = {"model": model, "input": prompt}
            try:
                # Keep retries low: a blocking D-Bus call times out after ~25s,
                # and the /api/embeddings fallback below still needs to run.
                with self._http_post(
                    f"{OLLAMA_BASE_URL}/api/embed", payload, timeout=15.0, max_retries=2
                ) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        embeddings = data.get("embeddings", [])
                        if embeddings:
                            return json.dumps(embeddings[0])
            except Exception as e:
                logger.debug("Embeddings endpoint failed, trying fallback: %s", e)

            # Fallback to /api/embeddings
            payload = {"model": model, "prompt": prompt}
            with self._http_post(
                f"{OLLAMA_BASE_URL}/api/embeddings", payload, timeout=15.0, max_retries=2
            ) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    return json.dumps(data.get("embedding", []))
        except Exception as e:
            return json.dumps({"error": str(e)})
        return "[]"
