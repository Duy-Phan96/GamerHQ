from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent


def get_commit() -> str:
    try:
        marker = ROOT / 'BUILD_COMMIT'
        value = marker.read_text(encoding='utf-8').strip() if marker.exists() else subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
        return value if re.fullmatch(r'[0-9a-f]{40,64}', value) else 'unknown'
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def release_notes() -> str:
    try:
        text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        section = text.split('\n## ', 2)[1]
        bullets = [line[2:180] for line in section.splitlines() if line.startswith('- ')][:3]
        return '\n'.join('• ' + line for line in bullets) or '• Maintenance and reliability improvements.'
    except (OSError, IndexError):
        return '• See the repository changelog for this release.'


def get_version() -> str:
    version_file = Path(__file__).resolve().parent / "VERSION"
    return version_file.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    print(get_version())
