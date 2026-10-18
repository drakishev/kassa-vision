"""Generate an artificial video; never downloads or copies real recordings."""
import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('data/dataset'))
    args = parser.parse_args()
    folder = args.dataset.resolve() / 'open/videos'
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / 'synthetic-demo.mp4'
    if output.exists():
        print('Synthetic demo already exists; nothing overwritten.')
        return
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-f', 'lavfi',
                    '-i', 'testsrc2=size=640x360:rate=25:duration=6',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '1',
                    '-movflags', '+faststart', str(output)], check=True)
    print('Created a six-second synthetic test pattern. It contains no real events.')


if __name__ == '__main__':
    main()
