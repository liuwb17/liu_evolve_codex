import os
from pathlib import Path
import tempfile
import unittest

from aad.v5_1_watchdog import task_progress


class WatchdogTests(unittest.TestCase):
    def test_live_process_without_progress_is_stalled_but_external_queue_is_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/'state.json'
            path.write_text('{}')
            os.utime(path,(100,100))
            self.assertEqual(task_progress(root,now=1400)['status'],'stalled')
            self.assertEqual(task_progress(root,now=1400,waiting_for_admission=True)['status'],
                             'waiting_for_evaluation_admission')

    def test_new_case_activity_counts_as_progress_before_state_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/'state.json'
            path.write_text('{}')
            os.utime(path,(100,100))
            leases=root/'measurements/cpu_leases'
            leases.mkdir(parents=True)
            os.utime(leases.parent,(100,100))
            os.utime(leases,(1395,1395))
            self.assertEqual(task_progress(root,now=1400),{'seconds_since_progress':5,'status':'running'})
