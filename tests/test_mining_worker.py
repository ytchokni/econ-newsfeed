from unittest.mock import MagicMock, patch

import pytest

from backend.discovery import mining_worker


@pytest.mark.parametrize("failure", [False, True])
def test_worker_runs_bounded_batch_then_waits_and_survives_failure(failure):
    stop = MagicMock()
    stop.is_set.side_effect = [False, True]
    with patch.object(mining_worker, "run_site_mining_batch") as run:
        if failure:
            run.side_effect = RuntimeError("Temporary DB failure")
        mining_worker.run_worker(stop)
    run.assert_called_once_with(apply=True)
    assert 3590 <= stop.wait.call_args.args[0] <= 3600


def test_stopped_worker_does_not_start_another_batch():
    stop = MagicMock()
    stop.is_set.return_value = True
    with patch.object(mining_worker, "run_site_mining_batch") as run:
        mining_worker.run_worker(stop)
    run.assert_not_called()


def test_worker_requires_positive_interval():
    with pytest.raises(ValueError):
        mining_worker.run_worker(interval_seconds=0)
