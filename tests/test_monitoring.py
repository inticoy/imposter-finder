import os
import unittest
from unittest.mock import patch
from imposter_finder import monitoring

class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ, {'BOT_ENV':'prod','MONITOR_ENDPOINT':'https://monitoring.example','RIOT_API_KEY':'secret-token-value'})
        self.env.start();monitoring.begin(['private-player'])
    def tearDown(self):
        self.env.stop();monitoring.begin()
    def test_account_success_does_not_erase_failure(self):
        monitoring.error('lol.accounts',RuntimeError('HTTP 403'),'조회 실패')
        monitoring.observe('lol.accounts')
        self.assertEqual(monitoring.events()[0]['code'],'http-403')
    def test_redaction_and_structural_traceback(self):
        try:
            raise RuntimeError('secret-token-value private-player https://private.example/feed')
        except RuntimeError as exc:
            monitoring.error('test',exc,'secret-token-value private-player')
        text=str(monitoring.events())
        self.assertNotIn('secret-token-value',text);self.assertNotIn('private-player',text);self.assertNotIn('private.example',text)
    def test_no_automatic_success_for_unchecked_component(self):
        monitoring.observe('lol.accounts')
        self.assertNotIn('lol.timeline',[e['component'] for e in monitoring.events()])

if __name__=='__main__':unittest.main()
