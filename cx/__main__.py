"""python -m cx -- launch the GUI app.

Headless cycles instead (for a launcher schedule / cron / testing):

  python -m cx --once         the hourly cycle: provision -> pull -> store
  python -m cx --actualize    the full cycle: + backfill, uniques, trade dict
"""
import sys


def _cycle(full: bool):
    import queue as _queue
    import threading

    from DATA.pipeline.runner import PipelineRunner
    from cx.stages import build_stages

    q: "_queue.Queue" = _queue.Queue()
    stages = build_stages(full)
    for s in stages:
        s._queue = q

    def show(msg):
        t = msg.get("type")
        if t == "log":
            print(f"  [{msg['stage']}] {msg['msg']}")
        elif t == "status":
            extra = f" count={msg['count']}" if msg.get("count") is not None else ""
            if msg.get("error"):
                extra += f" error={msg['error']}"
            print(f"[{msg['stage']}] {msg['status']}{extra}")
        # "progress" ticks (per-pair backfill) drive the GUI ring only

    stop = threading.Event()

    def drain():
        while not stop.is_set():
            try:
                show(q.get(timeout=0.1))
            except _queue.Empty:
                continue

    th = threading.Thread(target=drain, daemon=True)
    th.start()
    PipelineRunner(stages, q).run_cycle()
    stop.set()
    th.join(timeout=1)
    while True:                          # whatever landed after the drain stopped
        try:
            show(q.get_nowait())
        except _queue.Empty:
            break
    print("cycle done.")


def main():
    if "--actualize" in sys.argv:
        _cycle(full=True)
    elif "--once" in sys.argv:
        _cycle(full=False)
    else:
        from cx.ui.app import main as app_main
        app_main()


if __name__ == "__main__":
    main()
