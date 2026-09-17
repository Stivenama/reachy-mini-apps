import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import academic_research as research
import notebooklm_engine as nb


class ResearchTests(unittest.TestCase):
    def test_local_text_covers_start_and_end_without_source_context(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'misleading-title.txt'
            path.write_text('INICIO temple ' + 'acero ' * 2500 + ' FINAL enfriamiento', encoding='utf-8')
            with patch.object(nb, 'ask_notebook', return_value='{"topics":["temple del acero"]}') as ask:
                topics = research.transcript_topics('p', 'n', 's', path, lambda: False)
            prompts = '\n'.join(c.args[2] for c in ask.call_args_list)
            self.assertIn('INICIO temple', prompts)
            self.assertIn('FINAL enfriamiento', prompts)
            self.assertNotIn('misleading-title', prompts)
            self.assertEqual(topics, ['temple del acero'])

    def test_empty_local_file_does_not_call_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'empty.txt'
            path.write_text('', encoding='utf-8')
            with patch.object(nb, 'ask_notebook') as ask:
                with self.assertRaises(ValueError):
                    research.transcript_topics('p', 'n', 's', path, lambda: False)
                ask.assert_not_called()

    def test_local_path_used_in_discovery(self):
        with patch.object(nb, 'wait_source'), patch.object(research, 'transcript_topics', return_value=['templabilidad']) as local, patch.object(research, '_remote_topics', return_value=[]) as remote, patch.object(research, '_discover_topics', return_value=(None, None)):
            research.discover('p', 'n', 's', transcript_path='clase.txt')
            local.assert_called_once()
            remote.assert_called_once()

    def test_indexed_source_avoids_hundreds_of_requests(self):
        with patch.object(nb, 'wait_source') as wait, patch.object(research, '_remote_topics', return_value=['acero']), patch.object(research, 'transcript_topics') as local, patch.object(research, '_discover_topics', return_value=(None, None)):
            research.discover('p', 'n', 's', transcript_path='clase.txt')
            wait.assert_called_once()
            local.assert_not_called()

    def test_structured_cli_error_is_visible(self):
        message = nb._error_detail('{"error":true,"code":"NOTEBOOKLM_ERROR","message":"question too large"}', '')
        self.assertIn('question too large', message)

    def test_selection_keeps_original_url_without_extra_chat(self):
        url = 'https://openstax.org/books/' + 'a' * 2000
        with patch.object(nb, 'ask_notebook') as ask, patch.object(nb, 'research_discover', return_value=[{'url': url, 'title': 'Book'}]) as search:
            text, video = research._discover_topics('p', 'n', 's', ['acero'], lambda: False)
        self.assertEqual(text['url'], url)
        self.assertIsNone(video)
        ask.assert_not_called()
        self.assertEqual(search.call_count, 2)

    def test_all_chunk_prompts_fit_server_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'long.txt'
            path.write_text('Información académica. ' * 2000, encoding='utf-8')
            with patch.object(nb, 'ask_notebook', return_value='{"topics":["acero"]}') as ask:
                research.transcript_topics('p', 'n', 's', path, lambda: False)
            self.assertEqual(ask.call_count, 1)
            self.assertTrue(all(len(c.args[2]) < 3000 for c in ask.call_args_list))

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

    def test_many_candidates_still_only_two_searches_and_one_each(self):
        sources = [{'url': f'https://doi.org/10.1234/{i}', 'title': 'Paper'} for i in range(50)]
        sources.insert(0, {'url': 'https://youtube.com/watch?v=abcdefghijk', 'title': 'Tutorial'})
        result, ask, search = self.run_search(sources, {})
        self.assertEqual(result[0]['url'], 'https://doi.org/10.1234/0')
        self.assertEqual(result[1]['url'], 'https://youtube.com/watch?v=abcdefghijk')
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(search.call_count, 2)

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
