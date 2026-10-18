import json
import math
import subprocess
from fractions import Fraction


def probe_video(path, max_duration):
    try:
        output = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,width,height,avg_frame_rate:format=duration,format_name",
            "-of", "json", str(path)], check=True, timeout=10, capture_output=True)
        metadata = json.loads(output.stdout)
        streams = [s for s in metadata["streams"] if s["codec_type"] == "video"]
        stream = streams[0]
        duration = float(metadata["format"]["duration"])
        width, height = int(stream["width"]), int(stream["height"])
        fps = float(Fraction(stream["avg_frame_rate"]))
        if not math.isfinite(duration) or not 0 < duration <= max_duration:
            raise ValueError("Invalid or unsupported video duration")
        if not 0 < fps <= 240 or min(width, height) < 16 or width*height > 16_777_216:
            raise ValueError("Unsupported video dimensions or frame rate")
        if "mov" not in metadata["format"]["format_name"]:
            raise ValueError("Expected a MOV-compatible container")
        return {"duration_sec": duration, "width": width, "height": height, "fps": fps}
    except (subprocess.SubprocessError, ValueError, KeyError, IndexError, ZeroDivisionError) as exc:
        raise ValueError("Invalid or unsupported MOV video") from exc
