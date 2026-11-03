"""Offline regression tests: no real API calls or credentials required."""

import importlib.util
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

import httpx
from openai import OpenAI
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "agent-life-from-zero" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(content, finish_reason="stop", usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=usage,
    )


class ExamplesTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LLM_API_KEY": "test-key", "LLM_MODEL": "test-model"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.output = patch("sys.stdout", new_callable=io.StringIO)
        self.output.start()
        self.addCleanup(self.output.stop)
        self.chat = load("chatbot")
        self.extractor = load("extract_json")

    def extract(self, responses, retrys=2):
        client = MagicMock()
        client.chat.completions.create.side_effect = responses
        with patch.object(self.extractor, "OpenAI", return_value=client):
            result = self.extractor.extract("sample", retrys=retrys)
        return result, client

    def test_import_has_no_network_or_input_side_effects(self):
        with patch("openai.OpenAI") as constructor, patch("builtins.input") as user_input:
            load("chatbot")
            load("extract_json")
        constructor.assert_not_called()
        user_input.assert_not_called()

    def test_immediate_quit(self):
        with patch.object(self.chat, "OpenAI") as constructor, patch("builtins.input", return_value="quit"):
            self.chat.main()
        constructor.return_value.chat.completions.create.assert_not_called()

    def test_eof(self):
        with patch.object(self.chat, "OpenAI") as constructor, patch("builtins.input", side_effect=EOFError):
            self.chat.main()
        constructor.return_value.chat.completions.create.assert_not_called()

    def test_no_usage_or_text(self):
        client = MagicMock()
        client.chat.completions.create.return_value = response(None)
        with patch.object(self.chat, "OpenAI", return_value=client), patch("builtins.input", side_effect=["hi", "quit"]):
            self.chat.main()
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_real_sdk_with_offline_transport(self):
        # Exercise the locked SDK without network or environment proxy settings.
        def handler(request):
            return httpx.Response(200, json={
                "id": "test", "object": "chat.completion", "created": 0,
                "model": "test-model", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop"}],
            })
        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            with OpenAI(api_key="test", http_client=http_client) as client:
                with patch.object(self.chat, "OpenAI", return_value=client), patch(
                    "builtins.input", side_effect=["hi", "quit"]
                ):
                    self.chat.main()

    def test_missing_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                self.chat.main()
            with self.assertRaises(ValueError):
                self.extractor.extract("sample")

    def test_valid_object(self):
        raw = '{"sample":"A","measure":"OD600","value":0.82}'
        result, client = self.extract([response(raw)])
        self.assertEqual(result["value"], 0.82)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["response_format"], {"type": "json_object"})

    def test_invalid_results_retry(self):
        invalid = ["", " ", "not json", "[]", "null", "42", "true",
                   '{"sample":"A","value":1}',
                   '{"sample":"A","measure":42,"value":1}',
                   '{"sample":"A","measure":"OD600","value":true}',
                   '{"sample":"A","measure":"OD600","value":NaN}',
                   '{"sample":"A","measure":"OD600","value":Infinity}',
                   '{"sample":"A","measure":"OD600","value":1e999}']
        valid = response('{"sample":"B","measure":"OD600","value":1.37}')
        for raw in invalid:
            with self.subTest(raw=raw):
                result, client = self.extract([response(raw), valid])
                self.assertEqual(result["sample"], "B")
                self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_incomplete_result_retries_even_if_parseable(self):
        raw = '{"sample":"A","measure":"OD600","value":1}'
        result, client = self.extract([response(raw, "length"), response(raw)])
        self.assertEqual(result["value"], 1)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_retry_limit(self):
        client = MagicMock()
        client.chat.completions.create.return_value = response("[]")
        with patch.object(self.extractor, "OpenAI", return_value=client), self.assertRaises(ValueError):
            self.extractor.extract("sample", retrys=2)
        self.assertEqual(client.chat.completions.create.call_count, 3)

    def test_invalid_retry_count(self):
        for retries in [-1, True, 1.5]:
            with self.subTest(retries=retries), self.assertRaises(ValueError):
                self.extractor.extract("sample", retrys=retries)


if __name__ == "__main__":
    unittest.main()
