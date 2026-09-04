from pathlib import Path


def get_version() -> str:
    version_file = Path(__file__).resolve().parent / "VERSION"
    return version_file.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    print(get_version())
