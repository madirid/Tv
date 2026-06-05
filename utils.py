import subprocess

def ffmpeg_check(url):
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            url
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

        return result.returncode == 0

    except:
        return False


def clean_name(name):
    return name.replace("#EXTINF:-1,", "").strip()