"""brain_conversations.py — D-Bus API for conversations, long-term memories and the agent tool list.

Mixed into ``BrainService`` (see ``brain_service.py``); methods rely on its state
(``self.store``, ``self.config``, ``self.providers`` ...) set up in ``_setup``.
"""

import json
import logging

import dbus
import dbus.service
from brain_sanitize import _sanitize_context

logger = logging.getLogger("axon-brain")


class ConversationsMixin(dbus.service.Object):
    """D-Bus API for conversations, long-term memories and the agent tool list."""

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="s")
    def CreateConversation(self, system_prompt, title):
        conv_id = self.store.create_conversation(
            system_prompt=system_prompt if system_prompt else None, title=title if title else None
        )
        return conv_id

    @dbus.service.method("org.axonos.Brain", in_signature="sss", out_signature="b")
    def AddMessage(self, conversation_id, role, content):
        # "system" is not accepted: any session client could otherwise plant
        # persistent instructions in a conversation
        if role not in ("user", "assistant"):
            role = "user"
        self.store.add_message(conversation_id, role, content)
        return True

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="s")
    def GetMessages(self, conversation_id):
        messages = self.store.get_messages(conversation_id)
        return json.dumps(messages)

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def ListConversations(self):
        conversations = self.store.list_conversations()
        return json.dumps(conversations)

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="b")
    def DeleteConversation(self, conversation_id):
        self.store.delete_conversation(conversation_id)
        return True

    @dbus.service.method("org.axonos.Brain", in_signature="sb", out_signature="b")
    def ArchiveConversation(self, conversation_id, archived):
        """Archive or restore a conversation. Returns False if it doesn't exist."""
        return bool(self.store.set_archived(str(conversation_id), bool(archived)))

    @dbus.service.method("org.axonos.Brain", in_signature="ss", out_signature="b")
    def UpdateTitle(self, conversation_id, title):
        self.store.update_title(conversation_id, title)
        return True

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def ListTools(self):
        """Names and descriptions of the agent's tools."""
        return json.dumps(self.tools.describe())

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="s")
    def ListMemories(self):
        """Remembered facts, newest first."""
        return json.dumps(self.memory.entries())

    @dbus.service.method("org.axonos.Brain", in_signature="s", out_signature="x")
    def AddMemory(self, text):
        """Remember a fact. Returns its id, or -1 if it was rejected."""
        try:
            return self.memory.add(_sanitize_context(str(text)))
        except ValueError:
            return -1

    @dbus.service.method("org.axonos.Brain", in_signature="x", out_signature="b")
    def ForgetMemory(self, fact_id):
        """Delete one remembered fact."""
        return self.memory.forget(int(fact_id))

    @dbus.service.method("org.axonos.Brain", in_signature="", out_signature="b")
    def ClearMemories(self):
        """Delete every remembered fact."""
        self.memory.clear()
        return True
