import io
import json
import socket
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ai  # noqa: E402

VALID = {
    "match_score": 78,
    "matched_points": ["保持轻松语气"],
    "missing_points": ["没有给出边界"],
    "differences": ["语气更直接"],
    "explanation": "主要意思一致",
}


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def read(self):
        return self.text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def body_for(content):
    return json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"total_tokens": 10}})


def opener_with(content, captured=None, status=200):
    def opener(request, timeout=None):
        if captured is not None:
            captured["url"] = request.full_url
            captured["headers"] = {key.lower(): value for key, value in request.headers.items()}
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
        return FakeResponse(body_for(content), status)

    return opener


class ValidateAnalysisTest(unittest.TestCase):
    def test_accepts_valid_result_and_rounds_score(self):
        result = ai.validate_analysis({**VALID, "match_score": 78.6})
        self.assertEqual(result["match_score"], 79)
        self.assertEqual(result["matched_points"], ["保持轻松语气"])

    def test_accepts_string_where_list_is_expected(self):
        result = ai.validate_analysis({**VALID, "differences": "只有一句话的差异"})
        self.assertEqual(result["differences"], ["只有一句话的差异"])

    def test_drops_empty_items_and_caps_count(self):
        result = ai.validate_analysis({**VALID, "matched_points": ["", "  ", "有内容"]})
        self.assertEqual(result["matched_points"], ["有内容"])
        many = ai.validate_analysis({**VALID, "matched_points": [f"第{i}点" for i in range(10)]})
        self.assertEqual(len(many["matched_points"]), 6)

    def test_rejects_broken_results(self):
        broken = [
            None,
            "字符串",
            [],
            {**VALID, "match_score": "很高"},
            {**VALID, "match_score": True},
            {**VALID, "match_score": -5},
            {**VALID, "match_score": 101},
            {key: value for key, value in VALID.items() if key != "match_score"},
            {**VALID, "missing_points": 5},
            {**VALID, "missing_points": [1, 2]},
            {**VALID, "explanation": "   "},
        ]
        for item in broken:
            with self.subTest(item=item):
                with self.assertRaises(ai.AiError) as context:
                    ai.validate_analysis(item)
                self.assertEqual(context.exception.code, "invalid_model_output")

    def test_extract_json_text(self):
        self.assertEqual(ai.extract_json_text('```json\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(ai.extract_json_text('好的：{"a":1} 以上'), '{"a":1}')
        self.assertIsNone(ai.extract_json_text("没有 JSON"))
        self.assertIsNone(ai.extract_json_text(None))


class AnalyzeTest(unittest.TestCase):
    def test_missing_key_reports_not_configured_without_calling_network(self):
        calls = []

        def opener(request, timeout=None):
            calls.append(request)
            return FakeResponse(body_for(json.dumps(VALID)))

        with self.assertRaises(ai.AiError) as context:
            ai.analyze("正面", "标准回复", "我的回答", "", opener=opener)
        self.assertEqual(context.exception.code, "not_configured")
        self.assertFalse(context.exception.retriable)
        self.assertEqual(calls, [])

    def test_empty_answer_is_rejected_before_network(self):
        with self.assertRaises(ai.AiError) as context:
            ai.analyze("正面", "标准回复", "   ", "key", opener=opener_with(json.dumps(VALID)))
        self.assertEqual(context.exception.code, "bad_request")

    def test_successful_call_sends_expected_request(self):
        captured = {}
        outcome = ai.analyze(
            "女生说：「在吗？」",
            "在的，怎么啦？",
            "在的，怎么啦？",
            "test-key",
            model="deepseek-flash",
            thinking="disabled",
            opener=opener_with(json.dumps(VALID, ensure_ascii=False), captured),
        )

        self.assertEqual(outcome["analysis"]["match_score"], 78)
        self.assertEqual(outcome["model"], "deepseek-flash")
        self.assertEqual(captured["url"], ai.API_URL)
        self.assertEqual(captured["headers"]["authorization"], "Bearer test-key")
        body = captured["body"]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertFalse(body["stream"])
        self.assertIn("不评价用户的人格", body["messages"][0]["content"])
        self.assertIn("match_score", body["messages"][0]["content"])
        self.assertIn("在的，怎么啦？", body["messages"][1]["content"])

    def test_thinking_mode_is_passed_through(self):
        captured = {}
        ai.analyze("正面", "标准回复", "回答", "key", thinking="enabled", opener=opener_with(json.dumps(VALID), captured))
        self.assertEqual(captured["body"]["thinking"], {"type": "enabled"})

    def test_non_json_model_output_never_produces_a_score(self):
        with self.assertRaises(ai.AiError) as context:
            ai.analyze("正面", "标准回复", "回答", "key", opener=opener_with("我觉得你的回答挺好的，大概 80 分吧。"))
        self.assertEqual(context.exception.code, "invalid_model_output")
        self.assertNotIn("match_score", str(context.exception))

    def test_out_of_range_score_is_rejected(self):
        payload = json.dumps({**VALID, "match_score": 150})
        with self.assertRaises(ai.AiError) as context:
            ai.analyze("正面", "标准回复", "回答", "key", opener=opener_with(payload))
        self.assertIn("0-100", context.exception.message)

    def test_missing_field_is_rejected(self):
        payload = json.dumps({key: value for key, value in VALID.items() if key != "missing_points"})
        with self.assertRaises(ai.AiError) as context:
            ai.analyze("正面", "标准回复", "回答", "key", opener=opener_with(payload))
        self.assertIn("missing_points", context.exception.message)

    def test_empty_choices_is_reported(self):
        def opener(request, timeout=None):
            return FakeResponse(json.dumps({"choices": [], "usage": {}}))

        with self.assertRaises(ai.AiError) as context:
            ai.analyze("正面", "标准回复", "回答", "key", opener=opener)
        self.assertEqual(context.exception.code, "empty_model_output")

    def test_markdown_fenced_json_is_accepted(self):
        payload = "```json\n" + json.dumps(VALID) + "\n```"
        outcome = ai.analyze("正面", "标准回复", "回答", "key", opener=opener_with(payload))
        self.assertEqual(outcome["analysis"]["match_score"], 78)

    def test_http_errors_are_mapped_to_clear_messages(self):
        def raise_http(status, message):
            def opener(request, timeout=None):
                payload = json.dumps({"error": {"message": message}}).encode("utf-8")
                raise urllib.error.HTTPError(ai.API_URL, status, message, {}, io.BytesIO(payload))

            return opener

        cases = [(401, "unauthorized"), (403, "unauthorized"), (402, "upstream_error"), (429, "upstream_error"), (500, "upstream_error")]
        for status, code in cases:
            with self.subTest(status=status):
                with self.assertRaises(ai.AiError) as context:
                    ai.analyze("正面", "标准回复", "回答", "key", opener=raise_http(status, "Invalid token"))
                self.assertEqual(context.exception.code, code)
                self.assertIn("Invalid token", context.exception.message)

    def test_timeout_and_network_errors(self):
        def raise_timeout(request, timeout=None):
            raise socket.timeout("timed out")

        def raise_network(request, timeout=None):
            raise urllib.error.URLError("getaddrinfo failed")

        with self.assertRaises(ai.AiError) as timeout_context:
            ai.analyze("正面", "标准回复", "回答", "key", timeout=5, opener=raise_timeout)
        self.assertEqual(timeout_context.exception.code, "timeout")
        self.assertIn("5 秒", timeout_context.exception.message)

        with self.assertRaises(ai.AiError) as network_context:
            ai.analyze("正面", "标准回复", "回答", "key", opener=raise_network)
        self.assertEqual(network_context.exception.code, "network")

    def test_check_connection_uses_a_sample_card(self):
        captured = {}
        outcome = ai.check_connection("key", opener=opener_with(json.dumps(VALID), captured))
        self.assertEqual(outcome["analysis"]["match_score"], 78)
        self.assertIn("你是不是对每个女生都这么热情啊", captured["body"]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
