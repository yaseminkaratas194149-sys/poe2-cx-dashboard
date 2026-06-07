"""python -m cx — launch the GUI app.

`python -m cx --once` runs one headless pipeline cycle instead (for a launcher
schedule / cron / testing): provision -> pull -> store.
"""
import sys


def _once():
    import queue as _queue
    import threading

    from DATA.pipeline.runner import PipelineRunner
    from cx.stages import build_stages

    q: "_queue.Queue" = _queue.Queue()
    stages = build_stages()
    for s in stages:
        s._queue = q

    stop = threading.Event()

    def drain():
        while not stop.is_set():
            try:
                msg = q.get(timeout=0.1)
            except _queue.Empty:
                continue
            t = msg.get("type")
            if t == "log":
                print(f"  [{msg['stage']}] {msg['msg']}")
            elif t == "status":
                extra = f" count={msg['count']}" if msg.get("count") is not None else ""
                print(f"[{msg['stage']}] {msg['status']}{extra}")

    th = threading.Thread(target=drain, daemon=True)
    th.start()
    PipelineRunner(stages, q).run_cycle()
    stop.set()
    th.join(timeout=1)
    print("cycle done.")


def main():
    if "--once" in sys.argv:
        _once()
    else:
        from cx.ui.app import main as app_main
        app_main()


if __name__ == "__main__":
    main()
