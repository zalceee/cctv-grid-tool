import json
import urllib.request
import urllib.error
import hashlib
import os
from pathlib import Path

GITHUB_OWNER = "zalceee"
GITHUB_REPO = "cctv-grid-tool"
ASSET_NAME = "cgt.exe"


def get_sha256(file_path):
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def update_cgt():

    folder = Path(__file__).resolve().parent

    app_path = folder / "cgt.exe"
    temp_path = folder / "cgt_update.exe"

    api_url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    )

    try:
        print("Checking GitHub for updates...")

        request = urllib.request.Request(
            api_url,
            headers={"User-Agent": "CGT-Updater"}
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.loads(response.read())

        print("Latest release:", release["tag_name"])

        asset = next(
            (
                item for item in release["assets"]
                if item["name"].lower() == ASSET_NAME.lower()
            ),
            None
        )

        if not asset:
            raise Exception("cgt.exe not found in latest release.")

        # Get GitHub SHA256 checksum
        remote_digest = asset.get("digest")

        if remote_digest and remote_digest.startswith("sha256:"):
            remote_hash = remote_digest.split(":", 1)[1].lower()
        else:
            remote_hash = None

        # Compare local and GitHub files
        if app_path.exists() and remote_hash:

            print("Checking local file...")

            local_hash = get_sha256(app_path)

            if local_hash == remote_hash:
                print("Already up to date!")
                print("No download required.")

                os.startfile(str(app_path))
                return

            print("Update available!")
            print("Local file differs from GitHub.")

        else:
            print("No checksum available or local file missing.")
            print("Downloading release asset...")

        # Download latest version
        print("Downloading cgt.exe...")

        download_request = urllib.request.Request(
            asset["browser_download_url"],
            headers={"User-Agent": "CGT-Updater"}
        )

        with urllib.request.urlopen(
            download_request, timeout=120
        ) as response:

            with open(temp_path, "wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)

                    if not chunk:
                        break

                    file.write(chunk)

        if not temp_path.exists() or temp_path.stat().st_size == 0:
            raise Exception("Downloaded file is empty.")

        # Verify downloaded file
        downloaded_hash = get_sha256(temp_path)

        if remote_hash and downloaded_hash != remote_hash:
            raise Exception("SHA256 verification failed!")

        print("SHA256 verification successful.")

        # Replace old application
        print("Installing update...")

        os.replace(temp_path, app_path)

        print("Successfully updated cgt.exe!")

        # os.startfile(str(app_path))

    except PermissionError:
        print("Cannot replace cgt.exe.")
        print("Please close CCTV Grid Tool and try again.")

    except Exception as error:
        print("Update failed:", error)

    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

    input("Press Enter to exit...")


if __name__ == "__main__":
    update_cgt()
    sys.exit(0)
