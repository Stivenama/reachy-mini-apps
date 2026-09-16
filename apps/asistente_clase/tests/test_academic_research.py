import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import academic_research as research
import notebooklm_engine as nb


class ResearchTests(unittest.TestCase):
    def run_search(self, results, choice):
        plan = json.dumps({'topics': ['temple y templabilidad', 'transformaciones del acero']})
        with patch.object(nb, 'ask_notebook', side_effect=[plan, json.dumps(choice)]) as ask, patch.object(nb, 'research_discover', return_value=results) as search:
            result = research.discover('profile', 'notebook', 'transcript-source')
        return result, ask, search

    def test_content_drives_separate_searches(self):
        results = [{'url': 'https://doi.org/10.1234/example', 'title': 'Steel heat treatment'},
                   {'url': 'https://www.youtube.com/watch?v=abcdefghijk', 'title': 'University lecture'}]
        result, ask, search = self.run_search(results, {'text': {'id': 0, 'evidence': 'article'}, 'video': {'id': 1, 'evidence': 'lecture'}})
        self.assertIsNotNone(result[0])
        self.assertIsNotNone(result[1])
        self.assertEqual(search.call_count, 2)
        self.assertTrue(all('temple y templabilidad' in c.args[2] for c in search.call_args_list))
        self.assertTrue(all(c.kwargs['source_id'] == 'transcript-source' for c in ask.call_args_list))

    def test_no_web_page_fallback_for_video(self):
        result, _, _ = self.run_search([{'url': 'https://openstax.org/books/example', 'title': 'Book'}], {'text': {'id': 0, 'evidence': 'book'}, 'video': {'id': 0, 'evidence': 'book'}})
        self.assertIsNotNone(result[0])
        self.assertIsNone(result[1])

    def test_rejects_invented_ids_and_missing_evidence(self):
        result, _, _ = self.run_search([{'url': 'https://doi.org/10.1234/example'}], {'text': {'id': 90, 'evidence': 'x'}, 'video': {'id': 0}})
        self.assertEqual(result, (None, None))

    def test_no_low_quality_results(self):
        result, _, _ = self.run_search([{'url': 'https://blog.example.com/post'}, {'url': 'https://scholar.google.com/scholar?q=steel'}], {})
        self.assertEqual(result, (None, None))

    def test_hosts_and_video_urls(self):
        self.assertTrue(research.academic_host('https://repositorio.unal.edu.co/item'))
        self.assertFalse(research.academic_host('https://unal.edu.co.evil.com/item'))
        self.assertFalse(research.academic_host('https://nature.com.evil.com/item'))
        self.assertFalse(research.is_video('https://evil.com/?v=abcdefghijk'))
        self.assertFalse(research.is_video('https://youtube.com/shorts/abcdefghijk'))

    def test_empty_topics_never_search_by_title(self):
        with patch.object(nb, 'ask_notebook', return_value='{"topics":[]}'), patch.object(nb, 'research_discover') as search:
            with self.assertRaises(ValueError):
                research.discover('p', 'n', 's')
            search.assert_not_called()

    def test_cancel_prevents_search(self):
        with patch.object(nb, 'ask_notebook', return_value='{"topics":["acero"]}'), patch.object(nb, 'research_discover') as search:
            self.assertEqual(research.discover('p', 'n', 's', cancelled=lambda: True), (None, None))
            search.assert_not_called()

    def test_generation_style(self):
        with patch.object(nb, '_run', return_value='{}') as run:
            nb.generate('p', 'n', 'video', 'es', 900)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index('--style') + 1], 'classic')
        self.assertIn('transcripción', args[4])

    def test_long_prompt_file_removed(self):
        paths = []
        def fake_run(args, **kwargs):
            path = Path(args[args.index('--prompt-file') + 1])
            paths.append(path)
            self.assertEqual(path.read_text(encoding='utf-8'), 'á' * 40000)
            return '{"answer":"ok"}'
        with patch.object(nb, '_run', side_effect=fake_run):
            self.assertEqual(nb.ask_notebook('p', 'n', 'á' * 40000, 's'), 'ok')
        self.assertFalse(paths[0].exists())


if __name__ == '__main__':
    unittest.main()
