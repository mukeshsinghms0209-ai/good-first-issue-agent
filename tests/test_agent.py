import json

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import agent


class MockResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class AgentTests(unittest.TestCase):
    def test_loads_both_skills(self):
        reader, fixer = agent.load_skills()
        self.assertEqual(reader.name, "issue-reader")
        self.assertEqual(fixer.name, "code-fixer")
        self.assertIn("acceptance criteria", reader.instructions)
        self.assertIn("unified diff", fixer.instructions)

    def test_fetch_filters_pull_requests_and_sends_token(self):
        issues = [
            {"number": 12, "title": "Fix issue"},
            {"number": 13, "pull_request": {"url": "https://example.test/pr/13"}},
        ]
        captured = {}

        def fake_open(request, timeout=None):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            return MockResponse(json.dumps(issues).encode())

        with patch("agent.urllib.request.urlopen", side_effect=fake_open):
            result = agent.fetch_good_first_issues("owner/repo", 2, 5, "token")

        self.assertEqual([issue["number"] for issue in result], [12])
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertIn("labels=good+first+issue", captured["url"])

    def test_github_rate_limit_error_suggests_token(self):
        error = HTTPError(
            "https://api.github.com/repos/owner/repo",
            403,
            "Forbidden",
            {},
            BytesIO(b'{"message":"API rate limit exceeded"}'),
        )
        with patch("agent.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(agent.AgentError, "GITHUB_TOKEN"):
                agent.fetch_json(
                    "https://api.github.com/repos/owner/repo",
                    timeout=1,
                    headers={},
                )

    def test_fetch_repository_context_retrieves_relevant_files(self):
        responses = [
            {"default_branch": "main"},
            {
                "truncated": False,
                "tree": [
                    {
                        "path": "src/ReviewSettings.tsx",
                        "type": "blob",
                        "size": 20,
                    },
                    {"path": "secrets/token.py", "type": "blob", "size": 10},
                    {
                        "path": "node_modules/package/ReviewSettings.js",
                        "type": "blob",
                        "size": 10,
                    },
                ],
            },
            b"export const dirty = true;",
        ]
        requests = []

        def fake_open(request, timeout=None):
            requests.append(request)
            response = responses.pop(0)
            if isinstance(response, bytes):
                return MockResponse(response)
            return MockResponse(json.dumps(response).encode())

        with patch("agent.urllib.request.urlopen", side_effect=fake_open):
            context = agent.fetch_repository_context(
                "owner/repo", "ReviewSettings unsaved changes", 5, "token"
            )

        self.assertEqual(context[0]["path"], "src/ReviewSettings.tsx")
        self.assertIn("export const dirty", context[0]["content"])
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer token")
        self.assertEqual(requests[1].get_header("Authorization"), "Bearer token")
        self.assertIsNone(requests[2].get_header("Authorization"))
        self.assertIn("raw.githubusercontent.com", requests[2].full_url)

    def test_source_context_is_bounded_relevant_and_excludes_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src").mkdir()
            (root / "node_modules" / "package").mkdir(parents=True)
            (root / "secrets").mkdir()
            (root / "src" / "ReviewSettings.tsx").write_text(
                "export const ReviewSettings = true;", encoding="utf-8"
            )
            (root / "src" / "unrelated.py").write_text("print(1)", encoding="utf-8")
            (root / "node_modules" / "package" / "ReviewSettings.js").write_text(
                "dependency code", encoding="utf-8"
            )
            (root / "secrets" / "token.py").write_text(
                "private material", encoding="utf-8"
            )

            context = agent.load_repository_context(
                root, "ReviewSettings unsaved changes"
            )

        self.assertEqual(context[0]["path"], "src/ReviewSettings.tsx")
        context_paths = {entry["path"] for entry in context}
        self.assertNotIn("node_modules/package/ReviewSettings.js", context_paths)
        self.assertNotIn("secrets/token.py", context_paths)
        self.assertLessEqual(len(context), agent.MAX_REPOSITORY_CONTEXT_FILES)

    def test_skill_prompts_keep_issue_and_source_stages_separate(self):
        reader, fixer = agent.load_skills()
        issue = {
            "number": 3,
            "title": "Review settings",
            "body": "Prevent lost edits",
        }
        reader_prompt = agent.build_issue_reader_prompt(issue, reader)
        fixer_prompt = agent.build_code_fixer_prompt(
            issue,
            "Acceptance criteria: prevent losing unsaved changes.",
            fixer,
            [{"path": "src/ReviewSettings.tsx", "content": "const dirty = true;"}],
        )
        self.assertIn("issue-reader", reader_prompt)
        self.assertIn("Prevent lost edits", reader_prompt)
        self.assertNotIn("Local repository source context", reader_prompt)
        self.assertIn("code-fixer", fixer_prompt)
        self.assertIn("Acceptance criteria", fixer_prompt)
        self.assertIn("src/ReviewSettings.tsx", fixer_prompt)
        self.assertIn("untrusted", fixer_prompt)

    def test_ollama_requires_a_loopback_url(self):
        with patch("agent.urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(agent.AgentError, "remote inference"):
                agent.ask_ollama(
                    "prompt",
                    model="gemma3:1b",
                    base_url="https://example.com",
                    timeout=1,
                )
        urlopen.assert_not_called()

    def test_ollama_returns_response_from_local_api(self):
        response_body = json.dumps({"response": "proposed patch"}).encode()
        with patch(
            "agent.urllib.request.urlopen",
            return_value=MockResponse(response_body),
        ) as urlopen:
            result = agent.ask_ollama(
                "prompt",
                model="gemma3:1b",
                base_url="http://localhost:11434",
                timeout=5,
            )

        self.assertEqual(result, "proposed patch")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["model"], "gemma3:1b")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(request.full_url, "http://localhost:11434/api/generate")

    def test_ollama_connection_error_is_actionable(self):
        with patch(
            "agent.urllib.request.urlopen",
            side_effect=URLError("connection refused"),
        ):
            with self.assertRaisesRegex(agent.AgentError, "Start Ollama"):
                agent.ask_ollama(
                    "prompt",
                    model="gemma3:1b",
                    base_url="http://127.0.0.1:11434",
                    timeout=1,
                )

    def test_ollama_connection_reset_is_actionable(self):
        with patch(
            "agent.urllib.request.urlopen",
            side_effect=ConnectionResetError("connection reset"),
        ):
            with self.assertRaisesRegex(agent.AgentError, "Network error"):
                agent.ask_ollama(
                    "prompt",
                    model="gemma3:1b",
                    base_url="http://localhost:11434",
                    timeout=1,
                )


if __name__ == "__main__":
    unittest.main()
