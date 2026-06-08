import sys
import unittest
from unittest.mock import patch


class ChatInternalRetrieverTests(unittest.TestCase):
    def test_chat_module_does_not_import_tavily(self):
        sys.modules.pop("backend.chat.chat", None)
        with patch.dict(sys.modules, {"tavily": None}):
            from backend.chat.chat import ChatAgentWithMemory

        self.assertIsNotNone(ChatAgentWithMemory)


if __name__ == "__main__":
    unittest.main()
