"""Download the daily ridership headline file from data.gov.my (skipped if already present; --force to refresh)."""
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://storage.data.gov.my/transportation/ridership_headline.csv"
DEST = Path(__file__).resolve().parents[1] / "data" / "raw" / "ridership_headline.csv"


def main():
    if DEST.exists() and "--force" not in sys.argv:
        print(f"using existing {DEST.name} (pass --force to re-download)")
        return
    DEST.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):  # the CDN sometimes drops the connection
        try:
            urllib.request.urlretrieve(URL, DEST)
            break
        except OSError as e:
            print(f"retry {attempt + 1}: {e}")
            time.sleep(2)
    else:
        sys.exit("download failed")
    print(f"downloaded {DEST.name}: {sum(1 for _ in DEST.open(encoding='utf-8')) - 1} days")


if __name__ == "__main__":
    main()
