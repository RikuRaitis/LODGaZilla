"""Measure direct paired decode/download/scale throughput without GUI pacing."""
import argparse
import queue
import time

from .decoder import Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video')
    parser.add_argument('--frames', type=int, default=150)
    parser.add_argument('--max-side', type=int, default=1536)
    parser.add_argument('--hwaccel', default='auto')
    args = parser.parse_args()
    if args.frames < 1 or not 128 <= args.max_side <= 8192:
        parser.error('frames must be positive and max-side must be 128..8192')
    session = Session(args.video, args.max_side, args.hwaccel)
    started = time.perf_counter()
    try:
        kind, info = session.events.get(timeout=120)
        if kind != 'ready':
            raise RuntimeError(info)
        print(f"Backends: {info['backends']}; source {info['fps']:.2f} fps; "
              f"duration {info['duration']:.2f}s; "
              f"{info['metadata'].note if info['metadata'] else 'Standard video'}", flush=True)
        first = session.video.get(timeout=60)
        print(f'First frame: {time.perf_counter() - started:.2f}s', flush=True)
        started = time.perf_counter()
        count = 0
        while count < args.frames:
            try:
                picture = session.video.get(timeout=0.2)
                count += 1
            except queue.Empty:
                if session.video_eof == session.generation:
                    break
                while not session.events.empty():
                    kind, message = session.events.get_nowait()
                    if kind == 'error':
                        raise RuntimeError(message)
                if time.perf_counter() - started > 120:
                    raise TimeoutError('No complete benchmark within 120 seconds')
        elapsed = time.perf_counter() - started
        print(f'{count} pairs in {elapsed:.2f}s = {count / elapsed:.2f} fps; '
              f'lens upload {first.frames[0].width}x{first.frames[0].height}', flush=True)
    finally:
        session.close()
        for thread in session.threads:
            thread.join(10)


if __name__ == '__main__':
    main()
