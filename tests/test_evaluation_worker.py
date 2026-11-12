import unittest
from unittest.mock import patch, MagicMock


class EvaluationWorkerTests(unittest.TestCase):
    def test_dedicated_worker_does_not_run_api_restart_recovery(self):
        import sys
        from types import SimpleNamespace
        from cli.evaluation_worker import main as worker
        root=MagicMock()
        async def scheduler(concurrency):self.assertEqual(concurrency,3)
        module=SimpleNamespace(root_store=root,_evaluation_scheduler=scheduler)
        with patch.dict(sys.modules,{'backend.main':module}):worker(['--concurrency','3'])
        root.initialize.assert_not_called()
        root._session.assert_called_once()
