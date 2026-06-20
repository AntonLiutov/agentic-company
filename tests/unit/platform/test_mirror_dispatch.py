import threading
import time

from agentic_company.platform.mirror.mirror_dispatch import MirrorDispatcher


def test_same_key_is_serialised_never_overlapping():
    d = MirrorDispatcher(max_workers=4)
    active = []
    overlaps = []
    lock = threading.Lock()

    def task(tag):
        def run():
            with lock:
                active.append(tag)
                if len(active) > 1:
                    overlaps.append(tuple(active))
            time.sleep(0.05)
            with lock:
                active.remove(tag)

        return run

    for i in range(3):
        d.submit("A", task(f"a{i}"))
    assert d.flush("A", timeout=3) is True  # waits for the last (serialised) op
    d.shutdown(wait=True)
    assert overlaps == []  # two ops on the same work item never run at once


def test_different_keys_run_in_parallel():
    d = MirrorDispatcher(max_workers=4)
    barrier = threading.Barrier(3, timeout=3)
    ok = []

    def run():
        try:
            barrier.wait()  # only passes if 3 tasks are in flight together
            ok.append(1)
        except threading.BrokenBarrierError:
            pass

    for key in ("A", "B", "C"):
        d.submit(key, run)
    for key in ("A", "B", "C"):
        d.flush(key, timeout=3)
    d.shutdown(wait=True)
    assert len(ok) == 3  # all three keys ran concurrently


def test_flush_waits_then_handles_missing_and_timeout():
    d = MirrorDispatcher(max_workers=2)
    done = []
    d.submit("K", lambda: (time.sleep(0.05), done.append(1)))
    assert d.flush("K", timeout=3) is True
    assert done == [1]

    assert d.flush("NONE", timeout=0.1) is True  # nothing pending for the key

    block = threading.Event()
    d.submit("SLOW", lambda: block.wait(5))
    assert d.flush("SLOW", timeout=0.1) is False  # still running -> caller proceeds
    block.set()
    d.shutdown(wait=True)
