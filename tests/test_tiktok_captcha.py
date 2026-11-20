"""CAPTCHA pause tests, copied from the Crawl_Bus_SSUM repo's TikTok test files."""

import unittest
from unittest import mock

from crawlers.tiktok import crawl_tiktok as tt
from crawlers.tiktok import discover_tiktok as dt


class CaptchaPauseCrawlTests(unittest.TestCase):
    """crawl_video_with_captcha_pause: pause on demand, retry the video."""

    def _run(self, outcomes, interactive=True, solved=True, max_retries=None):
        from unittest import mock
        calls = []

        def fake_crawl_video(page, collector, url, **kwargs):
            calls.append(url)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with mock.patch.object(tt, "crawl_video", fake_crawl_video), \
             mock.patch.object(tt, "solve_captcha_interactively", return_value=solved) as solve:
            try:
                result = tt.crawl_video_with_captcha_pause(
                    object(), object(), "u", interactive, max_retries=max_retries)
            except tt.BlockedError:
                result = "blocked"
        return result, calls, solve

    def test_no_block_no_prompt(self):
        result, calls, solve = self._run([("rec", 1, "done")])
        self.assertEqual(result, ("rec", 1, "done"))
        solve.assert_not_called()

    def test_block_then_solved_retries_video(self):
        result, calls, solve = self._run([tt.BlockedError("x"), ("rec", 1, "done")])
        self.assertEqual(result, ("rec", 1, "done"))
        self.assertEqual(len(calls), 2)
        solve.assert_called_once()

    def test_not_interactive_raises_without_prompt(self):
        result, calls, solve = self._run([tt.BlockedError("x")], interactive=False)
        self.assertEqual(result, "blocked")
        solve.assert_not_called()

    def test_unsolved_raises(self):
        result, calls, solve = self._run([tt.BlockedError("x")], solved=False)
        self.assertEqual(result, "blocked")
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_max_retries(self):
        blocked = tt.BlockedError("x")
        result, calls, solve = self._run([blocked, blocked, blocked], max_retries=2)
        self.assertEqual(result, "blocked")
        self.assertEqual(len(calls), 3)
        self.assertEqual(solve.call_count, 2)


class _FakePage:
    """Minimal stand-in for a Playwright page. ``blocked_sequence`` holds one
    boolean per block check (check_block_markers queries
    ".captcha_verify_container" first, once per check); the last value repeats."""

    url = "https://www.tiktok.com/search/video?q=x"

    def __init__(self, blocked_sequence):
        self._blocked = list(blocked_sequence)
        self.checks = 0

    def query_selector(self, selector):
        if selector != ".captcha_verify_container":
            return None
        self.checks += 1
        blocked = self._blocked.pop(0) if len(self._blocked) > 1 else self._blocked[0]
        return object() if blocked else None


class CaptchaPauseTests(unittest.TestCase):
    def test_no_block_returns_false_without_prompting(self):
        page = _FakePage([False])
        with mock.patch("builtins.input") as fake_input:
            self.assertFalse(dt.check_block_or_wait(page, interactive=True))
        fake_input.assert_not_called()

    def test_not_interactive_still_raises_on_captcha(self):
        page = _FakePage([True])
        with mock.patch("builtins.input") as fake_input:
            with self.assertRaises(dt.BlockedError):
                dt.check_block_or_wait(page, interactive=False)
        fake_input.assert_not_called()

    def test_interactive_captcha_solved_returns_true(self):
        # blocked on the first check, clear on the re-check after the human pressed Enter
        page = _FakePage([True, False])
        with mock.patch("builtins.input", return_value=""):
            self.assertTrue(dt.check_block_or_wait(page, interactive=True))
        self.assertEqual(page.checks, 2)

    def test_interactive_unsolved_raises_blocked(self):
        page = _FakePage([True])  # never clears
        with mock.patch("builtins.input", return_value=""):
            with self.assertRaises(dt.BlockedError):
                dt.check_block_or_wait(page, interactive=True)

    def test_solve_gives_up_after_max_attempts(self):
        page = _FakePage([True])
        prompts = []
        solved = dt.solve_captcha_interactively(
            page, input_fn=lambda prompt="": prompts.append(prompt), max_attempts=3)
        self.assertFalse(solved)
        self.assertEqual(len(prompts), 3)

    def test_solve_succeeds_on_second_attempt(self):
        page = _FakePage([True, False])  # still blocked after the 1st Enter, clear after the 2nd
        prompts = []
        solved = dt.solve_captcha_interactively(
            page, input_fn=lambda prompt="": prompts.append(prompt), max_attempts=3)
        self.assertTrue(solved)
        self.assertEqual(len(prompts), 2)

    def test_solve_returns_false_when_no_console(self):
        def no_stdin(prompt=""):
            raise EOFError
        self.assertFalse(dt.solve_captcha_interactively(_FakePage([True]), input_fn=no_stdin))


if __name__ == "__main__":
    unittest.main()
