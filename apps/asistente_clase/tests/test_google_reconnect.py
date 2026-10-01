import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import google_auth as auth
import accounts


class ReconnectTests(unittest.TestCase):
    def test_revoked_session_shows_reconnect_button(self):
        creds = Mock(expired=True, refresh_token='test', valid=False)
        creds.refresh.side_effect = auth.RefreshError('invalid_grant')
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / 'token.json'
            token.write_text('old')
            with patch.object(auth.Credentials, 'from_authorized_user_file', return_value=creds):
                with self.assertRaisesRegex(RuntimeError, 'Conectar Classroom / Drive'):
                    auth.get_credentials(token, interactive=False)
            self.assertEqual(token.read_text(), 'old')

    def test_forced_login_replaces_valid_token_only_after_success(self):
        creds = Mock(granted_scopes=auth.SCOPES)
        creds.to_json.return_value = '{"test":"new"}'
        flow = Mock()
        flow.run_local_server.return_value = creds
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / 'token.json'
            token.write_text('old')
            with patch.object(auth.InstalledAppFlow, 'from_client_secrets_file', return_value=flow), patch.object(auth.Credentials, 'from_authorized_user_file') as load:
                auth.get_credentials(token, force_reauth=True)
                load.assert_not_called()
            self.assertEqual(token.read_text(), '{"test":"new"}')
            self.assertEqual(flow.run_local_server.call_args.kwargs['timeout_seconds'], 180)

    def test_cancelled_login_preserves_previous_token(self):
        flow = Mock()
        flow.run_local_server.side_effect = TimeoutError('timed out')
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / 'token.json'
            token.write_text('old')
            with patch.object(auth.InstalledAppFlow, 'from_client_secrets_file', return_value=flow):
                with self.assertRaises(TimeoutError):
                    auth.get_credentials(token, force_reauth=True)
            self.assertEqual(token.read_text(), 'old')

    def test_wrong_profile_never_opens_login(self):
        with patch.object(accounts.S, 'load', return_value={'profiles': {}}), patch.dict(accounts._status, {'running': False}), patch.object(accounts.threading, 'Thread') as thread:
            self.assertFalse(accounts.reconnect_google('unknown')[0])
            thread.assert_not_called()

    def test_reopening_active_login_resumes_without_second_thread(self):
        with patch.dict(accounts._status, {'running': True, 'step': 'google', 'profile_id': 'test'}), patch.object(accounts.threading, 'Thread') as thread:
            self.assertTrue(accounts.reconnect_google('test')[0])
            self.assertFalse(accounts.reconnect_google('other')[0])
            thread.assert_not_called()

    def test_authorization_link_is_published_before_credentials_are_saved(self):
        creds = Mock(granted_scopes=auth.SCOPES)
        creds.to_json.return_value = 'new'
        flow = Mock()
        flow.authorization_url.return_value = ('https://accounts.google.com/test', 'state')
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / 'token.json'
            token.write_text('old')
            links = []
            def callback(url):
                self.assertEqual(token.read_text(), 'old')
                links.append(url)
            def run(**kwargs):
                self.assertFalse(kwargs['open_browser'])
                self.assertIsNone(kwargs['authorization_prompt_message'])
                flow.authorization_url()
                return creds
            flow.run_local_server.side_effect = run
            with patch.object(auth.InstalledAppFlow, 'from_client_secrets_file', return_value=flow):
                auth.get_credentials(token, force_reauth=True, authorization_callback=callback)
            self.assertEqual(links, ['https://accounts.google.com/test'])
            self.assertEqual(token.read_text(), 'new')


if __name__ == '__main__':
    unittest.main()
