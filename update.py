
import sys
import json
import urllib.request
import hashlib
import os
from pathlib import Path

GITHUB_OWNER = "zalceee"
GITHUB_REPO = "cctv-grid-tool"

ASSET_NAME = "cgt.exe"
INFO_FILE = "cgt_info.txt"


def get_sha256(file_path):
    """Calculate SHA256 hash of a file."""

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def get_local_info(folder):
    """Read installed version and hash."""

    info_path = folder / INFO_FILE

    info = {
        "version": None,
        "sha256": None
    }

    if not info_path.exists():
        return info

    with open(info_path, "r", encoding="utf-8") as file:

        for line in file:

            if "=" not in line:
                continue

            key, value = line.strip().split("=", 1)

            if key in info:
                info[key] = value or None

    return info


def save_local_info(folder, version, sha256):
    """Save installed version and hash."""

    info_path = folder / INFO_FILE

    with open(info_path, "w", encoding="utf-8") as file:

        file.write(f"version={version}\n")
        file.write(f"sha256={sha256}\n")


def download_file(url, destination):

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CGT-Updater"}
    )

    with urllib.request.urlopen(request, timeout=120) as response:

        with open(destination, "wb") as file:

            while True:

                chunk = response.read(1024 * 1024)

                if not chunk:
                    break

                file.write(chunk)

    if not destination.exists() or destination.stat().st_size == 0:
        raise Exception("Downloaded file is empty.")


def update_cgt():

    folder = Path(__file__).resolve().parent

    app_path = folder / ASSET_NAME
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

        latest_version = release["tag_name"]

        # Find application asset
        asset = next(
            (
                item for item in release["assets"]
                if item["name"].lower() == ASSET_NAME.lower()
            ),
            None
        )

        if not asset:
            raise Exception("cgt.exe not found in latest release.")

        # Get GitHub SHA256
        remote_digest = asset.get("digest")

        remote_hash = None

        if remote_digest and remote_digest.startswith("sha256:"):
            remote_hash = remote_digest.split(":", 1)[1].lower()

        # Read local information
        local_info = get_local_info(folder)

        local_version = local_info["version"]
        saved_hash = local_info["sha256"]

        print("\nInstalled version:", local_version or "Unknown")
        print("Latest version:", latest_version)

        # Check whether download is needed
        if app_path.exists():

            if local_version == latest_version:

                if remote_hash:

                    print("\nChecking SHA256...")

                    local_hash = get_sha256(app_path)

                    if local_hash == remote_hash:

                        print("Already up to date!")
                        print("Version and SHA256 match.")

                        os.startfile(str(app_path))
                        return

                    print("SHA256 mismatch!")
                    print("Update required.")

                else:

                    print("\nGitHub SHA256 unavailable.")

                    if saved_hash:

                        local_hash = get_sha256(app_path)

                        if local_hash != saved_hash:
                            print("Local EXE has changed.")
                            print("Downloading latest release...")
                        else:
                            print("Version matches.")
                            print("No download required.")

                            os.startfile(str(app_path))
                            return

                    else:
                        print("No saved hash available.")
                        print("Downloading to establish file integrity.")

            else:

                print("\nNew version available!")
                print("Downloading update...")

        else:

            print("\nApplication not found.")
            print("Downloading application...")

        # Download application
        print("\nDownloading cgt.exe...")

        download_file(
            asset["browser_download_url"],
            temp_path
        )

        print("Download completed.")

        # Verify downloaded file if GitHub provides SHA256
        downloaded_hash = get_sha256(temp_path)

        if remote_hash:

            print("Verifying downloaded SHA256...")

            if downloaded_hash != remote_hash:
                raise Exception("SHA256 verification failed!")

            print("SHA256 verification successful.")

        else:

            print("GitHub SHA256 unavailable.")
            print("Downloaded file hash calculated locally.")

        # Replace old application
        print("\nInstalling update...")

        os.replace(temp_path, app_path)

        # Save version and hash
        save_local_info(
            folder,
            latest_version,
            downloaded_hash
        )

        print("\nSuccessfully updated CCTV Grid Tool!")
        print("Version:", latest_version)
        print("SHA256:", downloaded_hash)

        # os.startfile(str(app_path))

    except PermissionError:

        print("\nCannot replace cgt.exe.")
        print("Please close CCTV Grid Tool and try again.")

    except Exception as error:

        print("\nUpdate failed:", error)

    finally:

        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    update_cgt()
    sys.exit(0)
