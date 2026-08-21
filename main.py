import os
import math
import json
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from io import BytesIO

from dotenv import load_dotenv
import httpx
import xmltodict
from PIL import Image

# Load environment variables
load_dotenv()

NVR_IP = os.getenv("NVR_IP")
USERNAME = os.getenv("NVR_USERNAME")
PASSWORD = os.getenv("NVR_PASSWORD")
STORE_CODE = os.getenv("STORE_CODE")
STORE_NAME = os.getenv("STORE_NAME")

channels_env = os.getenv("CHANNELS", "")
CHANNELS = [int(ch.strip()) for ch in channels_env.split(",")] if channels_env else []

if not NVR_IP or not USERNAME or not PASSWORD or not CHANNELS:
    print("Error: Missing required environment configuration variables.")
    exit(1)

# Ensure uploads directory exists
uploads_dir = Path(STORE_CODE)
uploads_dir.mkdir(parents=True, exist_ok=True)

# Define Philippine Time (PHT) UTC+8
PHT = timezone(timedelta(hours=8))

# Gaps shorter than this are ignored.
# This prevents normal small file/segment boundaries from being reported as outages.
GAP_THRESHOLD_MINUTES = 5


# --- HELPER FUNCTIONS ---

def get_pht_iso_string(dt: datetime) -> str:
    """Converts a datetime object to a PHT (+08:00) formatted ISO string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PHT).isoformat()


def parse_dahua_response(text: str) -> dict:
    """Parses Dahua's plain text key=value response into a Python dictionary."""
    result = {}

    for line in text.strip().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()

    return result


def parse_hikvision_time(value: str):
    """Parse Hikvision UTC time returned in CMSearch results."""
    if not value:
        return None

    value = value.strip()

    # Normal Hikvision format: 2026-08-17T08:00:00Z
    try:
        return datetime.strptime(
            value, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # Handle ISO strings that include an offset.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_dahua_time(value: str):
    """Parse Dahua recording time in PHT."""
    if not value:
        return None

    value = value.strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=PHT)
        except ValueError:
            pass

    return None


def format_gap_duration(duration: timedelta) -> str:
    """Formats a timedelta into a readable duration."""
    total_seconds = max(0, int(duration.total_seconds()))

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")

    return " ".join(parts)


def detect_gaps(recordings):
    """
    Detects all gaps between recording segments.

    A gap is reported only when it is >= GAP_THRESHOLD_MINUTES.
    Returns a list so channels with many gaps can report all of them.
    """
    if not recordings:
        return []

    # Remove invalid segments.
    valid = [
        item for item in recordings
        if item.get("start") is not None and item.get("end") is not None
    ]

    if len(valid) < 2:
        return []

    # Sort by start time.
    valid.sort(key=lambda item: item["start"])

    # Merge overlapping or touching segments.
    merged = []

    for item in valid:
        start = item["start"]
        end = item["end"]

        if end < start:
            continue

        if not merged:
            merged.append({"start": start, "end": end})
            continue

        previous = merged[-1]

        if start <= previous["end"]:
            if end > previous["end"]:
                previous["end"] = end
        else:
            merged.append({"start": start, "end": end})

    gaps = []
    threshold = timedelta(minutes=GAP_THRESHOLD_MINUTES)

    for previous, current in zip(merged, merged[1:]):
        gap_start = previous["end"]
        gap_end = current["start"]
        gap_duration = gap_end - gap_start

        if gap_duration >= threshold:
            gaps.append({
                "start": get_pht_iso_string(gap_start),
                "end": get_pht_iso_string(gap_end),
                "duration": format_gap_duration(gap_duration)
            })

    return gaps


# --- BRAND DETECTION ---

async def detect_nvr_brand(client: httpx.AsyncClient) -> str:
    """Probes the NVR to automatically detect if it is Hikvision or Dahua."""
    print(f"Probing {NVR_IP} to detect NVR brand...")

    # 1. Probe for Hikvision (ISAPI)
    try:
        res_hik = await client.get(
            f"http://{NVR_IP}/ISAPI/System/deviceInfo"
        )

        if res_hik.status_code == 200:
            return "HIKVISION"

    except Exception:
        pass

    # 2. Probe for Dahua (CGI)
    try:
        res_dah = await client.get(
            f"http://{NVR_IP}/cgi-bin/magicBox.cgi?action=getSystemInfo"
        )

        if res_dah.status_code == 200:
            return "DAHUA"

    except Exception:
        pass

    return "UNKNOWN"


# --- SNAPSHOT ---

async def get_channel_snapshot(
    client: httpx.AsyncClient,
    channel: int,
    brand: str
):
    """Captures and resizes a JPEG snapshot based on the detected brand."""

    if brand == "HIKVISION":
        url = f"http://{NVR_IP}/ISAPI/Streaming/channels/{channel}/picture"
    else:
        url = f"http://{NVR_IP}/cgi-bin/snapshot.cgi?channel={channel}"

    try:
        response = await client.get(url)

        if response.status_code != 200:
            print(
                f"[Channel {channel}] Snapshot fetch failed "
                f"with status {response.status_code}"
            )
            return None

        img = Image.open(BytesIO(response.content))
        return img.resize((640, 360), Image.Resampling.LANCZOS)

    except Exception as e:
        print(f"[Channel {channel}] Snapshot error: {str(e)}")
        return None


# --- HIKVISION RECORDING SEARCH ---

async def get_hikvision_recordings(
    client: httpx.AsyncClient,
    channel: int,
    utc_now: datetime
):
    """
    Gets recording segments from Hikvision for the last 100 days.

    Uses a large maxResults value so we can inspect multiple segments
    and identify all significant gaps.
    """

    utc_hundred_ago = utc_now - timedelta(days=100)

    start_time = utc_hundred_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_time = utc_now.strftime("%Y-%m-%dT%H:%M:%SZ")
    unique_search_id = str(uuid.uuid4())

    xml_payload = f"""
    <CMSearchDescription>
        <searchID>{unique_search_id}</searchID>
        <trackList>
            <trackID>{channel}</trackID>
        </trackList>
        <timeSpanList>
            <timeSpan>
                <startTime>{start_time}</startTime>
                <endTime>{end_time}</endTime>
            </timeSpan>
        </timeSpanList>
        <maxResults>1000</maxResults>
    </CMSearchDescription>
    """.strip()

    url = f"http://{NVR_IP}/ISAPI/ContentMgmt/search"

    response = await client.post(
        url,
        content=xml_payload,
        headers={"Content-Type": "application/xml"}
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Hikvision search failed with HTTP {response.status_code}"
        )

    result = xmltodict.parse(response.text)

    match_list = result.get("CMSearchResult", {}).get("matchList", {})

    if not match_list:
        return []

    match_items = match_list.get("searchMatchItem", [])

    if not isinstance(match_items, list):
        match_items = [match_items]

    recordings = []

    for item in match_items:
        time_span = item.get("timeSpan", {})

        start_value = time_span.get("startTime")
        end_value = time_span.get("endTime")

        start_dt = parse_hikvision_time(start_value)
        end_dt = parse_hikvision_time(end_value)

        if start_dt and end_dt and end_dt >= start_dt:
            recordings.append({
                "start": start_dt,
                "end": end_dt
            })

    return recordings


# --- DAHUA RECORDING SEARCH ---

async def get_dahua_recordings(
    client: httpx.AsyncClient,
    channel: int,
    now: datetime
):
    """
    Gets recording segments from Dahua for the last 100 days.

    Dahua returns multiple files through findNextFile. We keep requesting
    pages until all available recording files have been collected.
    """

    hundred_days_ago = now - timedelta(days=100)

    start_str = hundred_days_ago.strftime("%Y-%m-%d %H:%M:%S")
    end_str = now.strftime("%Y-%m-%d %H:%M:%S")

    create_url = (
        f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi"
        f"?action=factory.create"
    )

    res_create = await client.get(create_url)
    obj_id = parse_dahua_response(res_create.text).get("result")

    if not obj_id:
        raise RuntimeError("Failed to create Dahua search session")

    try:
        cond_url = (
            f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi"
            f"?action=findFile"
            f"&object={obj_id}"
            f"&condition.Channel={channel}"
            f"&condition.StartTime={start_str}"
            f"&condition.EndTime={end_str}"
            f"&condition.Types[0]=dav"
        )

        res_find = await client.get(cond_url)

        if res_find.status_code != 200:
            raise RuntimeError(
                f"Dahua findFile failed with HTTP {res_find.status_code}"
            )

        recordings = []

        # Request multiple results per page.
        page_size = 100

        while True:
            next_url = (
                f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi"
                f"?action=findNextFile"
                f"&object={obj_id}"
                f"&count={page_size}"
            )

            res_next = await client.get(next_url)
            parsed_next = parse_dahua_response(res_next.text)

            found = int(parsed_next.get("found", "0") or "0")

            if found <= 0:
                break

            for index in range(found):
                start_key = f"items[{index}].StartTime"
                end_key = f"items[{index}].EndTime"

                start_dt = parse_dahua_time(
                    parsed_next.get(start_key)
                )
                end_dt = parse_dahua_time(
                    parsed_next.get(end_key)
                )

                if start_dt and end_dt and end_dt >= start_dt:
                    recordings.append({
                        "start": start_dt,
                        "end": end_dt
                    })

            # If fewer records than requested were returned,
            # this is normally the final page.
            if found < page_size:
                break

        return recordings

    finally:
        destroy_url = (
            f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi"
            f"?action=factory.destroy&object={obj_id}"
        )

        try:
            await client.get(destroy_url)
        except Exception:
            pass


# --- RETENTION + GAP DETECTION ---

async def get_channel_retention(
    client: httpx.AsyncClient,
    channel: int,
    brand: str
):
    """
    Calculates retention metadata and detects all significant recording gaps.
    """

    now = datetime.now(PHT)

    try:
        if brand == "HIKVISION":

            utc_now = datetime.now(timezone.utc)

            recordings = await get_hikvision_recordings(
                client,
                channel,
                utc_now
            )

            if not recordings:
                return {
                    "hasRecording": False,
                    "retentionDays": 0,
                    "message": "No recording found",
                    "gap": []
                }

            recordings.sort(key=lambda item: item["start"])

            oldest_date = recordings[0]["start"]

            diff_time = utc_now - oldest_date

            gaps = detect_gaps(recordings)

            return {
                "storeName": STORE_NAME,
                "hasRecording": True,
                "oldestRecording": get_pht_iso_string(oldest_date),
                # Keep the original behavior: latestRecording means current time.
                "latestRecording": get_pht_iso_string(now),
                "retentionDays": math.ceil(
                    diff_time.total_seconds() / (24 * 3600)
                ),
                "gap": gaps
            }

        elif brand == "DAHUA":

            recordings = await get_dahua_recordings(
                client,
                channel,
                now
            )

            if not recordings:
                return {
                    "hasRecording": False,
                    "retentionDays": 0,
                    "message": "No recording found",
                    "gap": []
                }

            recordings.sort(key=lambda item: item["start"])

            oldest_date = recordings[0]["start"]

            diff_time = now - oldest_date

            gaps = detect_gaps(recordings)

            return {
                "storeName": STORE_NAME,
                "hasRecording": True,
                "oldestRecording": get_pht_iso_string(oldest_date),
                # Keep the original behavior: latestRecording means current time.
                "latestRecording": get_pht_iso_string(now),
                "retentionDays": math.ceil(
                    diff_time.total_seconds() / (24 * 3600)
                ),
                "gap": gaps
            }

        return {
            "hasRecording": False,
            "retentionDays": 0,
            "message": "Unknown NVR brand",
            "gap": []
        }

    except Exception as e:
        print(f"[Channel {channel}] Retention error: {str(e)}")

        return {
            "hasRecording": False,
            "retentionDays": 0,
            "gap": [],
            "status": "Failed parsing retention details",
            "error": str(e)
        }


# --- MAIN EXECUTION LOGIC ---

async def generate_collage_and_retention():

    # Both Hikvision and Dahua use Digest Auth for secure API access.
    auth = httpx.DigestAuth(USERNAME, PASSWORD)

    pht_now = datetime.now(PHT)
    pht_date_stamp = pht_now.strftime("%Y-%m-%d")

    async with httpx.AsyncClient(
        auth=auth,
        timeout=30.0
    ) as client:

        # Detect the brand before doing anything else.
        brand = await detect_nvr_brand(client)

        if brand == "UNKNOWN":
            print(
                f"❌ Error: Could not determine NVR brand for "
                f"{NVR_IP}. Check credentials or IP."
            )
            return

        print(f"✅ Detected {brand} NVR!")
        print(
            f"Processing feeds, retention, and recording gaps "
            f"for {len(CHANNELS)} channels..."
        )

        tasks = []

        for channel in CHANNELS:
            task = asyncio.gather(
                get_channel_snapshot(
                    client,
                    channel,
                    brand
                ),
                get_channel_retention(
                    client,
                    channel,
                    brand
                )
            )

            tasks.append(task)

        results = await asyncio.gather(*tasks)

    valid_snapshots = []
    retention_data = {}

    for i, (snapshot, retention) in enumerate(results):

        channel = CHANNELS[i]

        if retention.get("hasRecording") is True:
            retention_data[f"channel_{channel}"] = retention

        if snapshot is not None:
            valid_snapshots.append(snapshot)

    # Save retention data even if all snapshots failed.
    if not valid_snapshots:

        print("⚠️ No valid image feeds found.")
        print("💾 Saving retention data only.")

        json_path = (
            uploads_dir
            / f"{pht_date_stamp}-{brand.lower()}-retention.txt"
        )

        # Delete previous day's retention files for this brand,
        # even though no new collage image was generated.
        for old_file in uploads_dir.glob(
            f"*-{brand.lower()}-retention.txt"
        ):
            if old_file != json_path:
                old_file.unlink()

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(
                retention_data,
                f,
                indent=4,
                ensure_ascii=False
            )

        print(f"📁 Retention data saved: {json_path}")
        return

    # Dynamic Canvas Grid Math.
    tile_width = 640
    tile_height = 360

    cols = math.ceil(math.sqrt(len(valid_snapshots)))
    rows = math.ceil(len(valid_snapshots) / cols)

    # Generate final composite collage using Pillow.
    collage = Image.new(
        "RGB",
        (cols * tile_width, rows * tile_height),
        (0, 0, 0)
    )

    for index, img in enumerate(valid_snapshots):

        x = (index % cols) * tile_width
        y = (index // cols) * tile_height

        collage.paste(img, (x, y))

    # Dynamic file naming based on the brand.
    image_path = (
        uploads_dir
        / f"{pht_date_stamp}-{brand.lower()}-collage.jpg"
    )

    json_path = (
        uploads_dir
        / f"{pht_date_stamp}-{brand.lower()}-retention.txt"
    )

    # Delete previous day's files for this brand.
    for old_file in uploads_dir.glob(
        f"*-{brand.lower()}-collage.jpg"
    ):
        if old_file != image_path:
            old_file.unlink()

    for old_file in uploads_dir.glob(
        f"*-{brand.lower()}-retention.txt"
    ):
        if old_file != json_path:
            old_file.unlink()

    # Save JPEG with 85% quality.
    collage.save(
        image_path,
        "JPEG",
        quality=85
    )

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            retention_data,
            f,
            indent=4,
            ensure_ascii=False
        )

    print("\n✅ Success! Collage and retention data generated.")
    print(f"📁 Image Saved: {image_path}")
    print(f"📁 JSON Saved: {json_path}\n")


if __name__ == "__main__":
    asyncio.run(generate_collage_and_retention())