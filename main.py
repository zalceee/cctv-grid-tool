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


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()

NVR_IP = os.getenv("NVR_IP")
USERNAME = os.getenv("NVR_USERNAME")
PASSWORD = os.getenv("NVR_PASSWORD")
STORE_CODE = os.getenv("STORE_CODE")
STORE_NAME = os.getenv("STORE_NAME")

channels_env = os.getenv("CHANNELS", "")

CHANNELS = [
    int(ch.strip())
    for ch in channels_env.split(",")
] if channels_env else []


if not NVR_IP or not USERNAME or not PASSWORD or not CHANNELS:
    print("Error: Missing required environment configuration variables.")
    exit(1)


# ============================================================
# DIRECTORIES
# ============================================================

uploads_dir = Path(STORE_CODE)
uploads_dir.mkdir(parents=True, exist_ok=True)


# ============================================================
# TIMEZONE
# ============================================================

# Philippine Time
PHT = timezone(timedelta(hours=8))


# ============================================================
# CONFIGURATION
# ============================================================

# Gaps shorter than this are ignored.
GAP_THRESHOLD_MINUTES = 5

# Hikvision retention search period.
#
# Previously this was 100 days.
# Increased to 150 days so retention older than 100 days
# can also be detected.
HIKVISION_SEARCH_DAYS = 150

# Number of Hikvision search results per page.
HIKVISION_PAGE_SIZE = 1000

# Safety limit to prevent infinite pagination.
HIKVISION_MAX_RESULTS = 100000


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_pht_iso_string(dt: datetime) -> str:
    """
    Converts a datetime object to PHT (+08:00)
    formatted ISO string.
    """

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(PHT).isoformat()


# ============================================================
# DAHUA RESPONSE PARSER
# ============================================================

def parse_dahua_response(text: str) -> dict:
    """
    Parses Dahua's plain text key=value response
    into a Python dictionary.
    """

    result = {}

    for line in text.strip().splitlines():

        if "=" in line:

            key, value = line.split("=", 1)

            result[key.strip()] = value.strip()

    return result


# ============================================================
# HIKVISION TIME PARSER
# ============================================================

def parse_hikvision_time(value: str):
    """
    Parses Hikvision UTC time returned in CMSearch results.
    """

    if not value:
        return None

    value = value.strip()

    # Normal Hikvision format:
    # 2026-08-17T08:00:00Z

    try:

        return datetime.strptime(
            value,
            "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)

    except ValueError:
        pass

    # Handle ISO strings that include an offset.

    try:

        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

    except ValueError:

        return None


# ============================================================
# DAHUA TIME PARSER
# ============================================================

def parse_dahua_time(value: str):
    """
    Parse Dahua recording time in PHT.
    """

    if not value:
        return None

    value = value.strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                value,
                fmt
            ).replace(tzinfo=PHT)

        except ValueError:
            pass

    return None


# ============================================================
# FORMAT GAP DURATION
# ============================================================

def format_gap_duration(duration: timedelta) -> str:
    """
    Formats a timedelta into a readable duration.
    """

    total_seconds = max(
        0,
        int(duration.total_seconds())
    )

    days, remainder = divmod(
        total_seconds,
        86400
    )

    hours, remainder = divmod(
        remainder,
        3600
    )

    minutes, _ = divmod(
        remainder,
        60
    )

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes or not parts:
        parts.append(f"{minutes}m")

    return " ".join(parts)


# ============================================================
# GAP DETECTION
# ============================================================

def detect_gaps(recordings):
    """
    Detects all gaps between recording segments.

    A gap is reported only when it is >=
    GAP_THRESHOLD_MINUTES.

    Returns a list so channels with many gaps
    can report all of them.
    """

    if not recordings:
        return []

    # Remove invalid segments.

    valid = [
        item
        for item in recordings
        if (
            item.get("start") is not None
            and item.get("end") is not None
        )
    ]

    if len(valid) < 2:
        return []

    # Sort by start time.

    valid.sort(
        key=lambda item: item["start"]
    )

    # Merge overlapping or touching segments.

    merged = []

    for item in valid:

        start = item["start"]
        end = item["end"]

        if end < start:
            continue

        if not merged:

            merged.append({
                "start": start,
                "end": end
            })

            continue

        previous = merged[-1]

        if start <= previous["end"]:

            if end > previous["end"]:
                previous["end"] = end

        else:

            merged.append({
                "start": start,
                "end": end
            })

    # Detect gaps.

    gaps = []

    threshold = timedelta(
        minutes=GAP_THRESHOLD_MINUTES
    )

    for previous, current in zip(
        merged,
        merged[1:]
    ):

        gap_start = previous["end"]
        gap_end = current["start"]

        gap_duration = gap_end - gap_start

        if gap_duration >= threshold:

            gaps.append({
                "start": get_pht_iso_string(
                    gap_start
                ),

                "end": get_pht_iso_string(
                    gap_end
                ),

                "duration": format_gap_duration(
                    gap_duration
                )
            })

    return gaps


# ============================================================
# BRAND DETECTION
# ============================================================

async def detect_nvr_brand(
    client: httpx.AsyncClient
) -> str:

    """
    Probes the NVR to automatically detect
    if it is Hikvision or Dahua.
    """

    print(
        f"Probing {NVR_IP} to detect NVR brand..."
    )

    # --------------------------------------------------------
    # HIKVISION
    # --------------------------------------------------------

    try:

        res_hik = await client.get(
            f"http://{NVR_IP}/ISAPI/System/deviceInfo"
        )

        if res_hik.status_code == 200:

            return "HIKVISION"

    except Exception:
        pass


    # --------------------------------------------------------
    # DAHUA
    # --------------------------------------------------------

    try:

        res_dah = await client.get(
            f"http://{NVR_IP}/cgi-bin/"
            f"magicBox.cgi?action=getSystemInfo"
        )

        if res_dah.status_code == 200:

            return "DAHUA"

    except Exception:
        pass


    return "UNKNOWN"


# ============================================================
# SNAPSHOT
# ============================================================

async def get_channel_snapshot(
    client: httpx.AsyncClient,
    channel: int,
    brand: str
):

    """
    Captures and resizes a JPEG snapshot
    based on the detected brand.
    """

    if brand == "HIKVISION":

        url = (
            f"http://{NVR_IP}"
            f"/ISAPI/Streaming/channels/"
            f"{channel}/picture"
        )

    else:

        url = (
            f"http://{NVR_IP}"
            f"/cgi-bin/snapshot.cgi"
            f"?channel={channel}"
        )

    try:

        response = await client.get(url)

        if response.status_code != 200:

            print(
                f"[Channel {channel}] "
                f"Snapshot fetch failed "
                f"with status "
                f"{response.status_code}"
            )

            return None

        img = Image.open(
            BytesIO(response.content)
        )

        return img.resize(
            (640, 360),
            Image.Resampling.LANCZOS
        )

    except Exception as e:

        print(
            f"[Channel {channel}] "
            f"Snapshot error: {str(e)}"
        )

        return None


# ============================================================
# HIKVISION RECORDING SEARCH
# ============================================================

async def get_hikvision_recordings(
    client: httpx.AsyncClient,
    channel: int,
    utc_now: datetime
):
    """
    Gets recording segments from Hikvision.

    Searches the last HIKVISION_SEARCH_DAYS days.

    Handles pagination using searchResultPosition
    so the automation can find recordings beyond
    the first 1,000 results.

    Returns:
        list of dictionaries containing:
            start
            end
    """

    utc_start = (
        utc_now
        - timedelta(days=HIKVISION_SEARCH_DAYS)
    )

    start_time = utc_start.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    end_time = utc_now.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    unique_search_id = str(
        uuid.uuid4()
    )

    url = (
        f"http://{NVR_IP}"
        f"/ISAPI/ContentMgmt/search"
    )

    page_size = HIKVISION_PAGE_SIZE

    search_position = 0

    recordings = []

    print(
        f"[Channel {channel}] "
        f"Hikvision search:"
    )

    print(
        f"[Channel {channel}] "
        f"From: {start_time}"
    )

    print(
        f"[Channel {channel}] "
        f"To:   {end_time}"
    )

    while True:

        # ----------------------------------------------------
        # SEARCH REQUEST
        # ----------------------------------------------------

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

    <maxResults>{page_size}</maxResults>

    <searchResultPosition>{search_position}</searchResultPosition>
</CMSearchDescription>
        """.strip()

        try:

            response = await client.post(
                url,
                content=xml_payload,
                headers={
                    "Content-Type": "application/xml"
                }
            )

        except Exception as e:

            raise RuntimeError(
                "Hikvision search request failed: "
                f"{e}"
            )

        if response.status_code != 200:

            raise RuntimeError(
                "Hikvision search failed with "
                f"HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        # ----------------------------------------------------
        # PARSE XML
        # ----------------------------------------------------

        try:

            result = xmltodict.parse(
                response.text
            )

        except Exception as e:

            raise RuntimeError(
                "Failed to parse Hikvision "
                f"XML response: {e}"
            )

        search_result = result.get(
            "CMSearchResult",
            {}
        )

        if not search_result:

            print(
                f"[Channel {channel}] "
                f"Empty Hikvision search result."
            )

            break

        # ----------------------------------------------------
        # MATCH LIST
        # ----------------------------------------------------

        match_list = search_result.get(
            "matchList",
            {}
        )

        if not match_list:

            print(
                f"[Channel {channel}] "
                f"No more recording results."
            )

            break

        match_items = match_list.get(
            "searchMatchItem",
            []
        )

        # Hikvision may return one dictionary instead
        # of a list when there is only one result.

        if not isinstance(
            match_items,
            list
        ):

            match_items = [
                match_items
            ]

        page_recordings = []

        # ----------------------------------------------------
        # PROCESS CURRENT PAGE
        # ----------------------------------------------------

        for item in match_items:

            time_span = item.get(
                "timeSpan",
                {}
            )

            start_value = time_span.get(
                "startTime"
            )

            end_value = time_span.get(
                "endTime"
            )

            start_dt = parse_hikvision_time(
                start_value
            )

            end_dt = parse_hikvision_time(
                end_value
            )

            if (
                start_dt is not None
                and end_dt is not None
                and end_dt >= start_dt
            ):

                page_recordings.append({
                    "start": start_dt,
                    "end": end_dt
                })

        recordings.extend(
            page_recordings
        )

        # ----------------------------------------------------
        # DEBUG INFORMATION
        # ----------------------------------------------------

        print(
            f"[Channel {channel}] "
            f"Position {search_position}: "
            f"{len(page_recordings)} valid "
            f"recordings"
        )

        # ----------------------------------------------------
        # NUMBER OF TOTAL MATCHES
        # ----------------------------------------------------

        num_matches = search_result.get(
            "numOfMatches"
        )

        try:

            num_matches = int(
                num_matches
            )

        except (
            TypeError,
            ValueError
        ):

            num_matches = None

        # ----------------------------------------------------
        # PAGINATION
        # ----------------------------------------------------

        if num_matches is not None:

            next_position = (
                search_position
                + len(match_items)
            )

            if next_position >= num_matches:

                break

            search_position = next_position

        else:

            # If Hikvision doesn't provide
            # numOfMatches, use page size.

            if len(match_items) < page_size:

                break

            search_position += len(
                match_items
            )

        # ----------------------------------------------------
        # SAFETY LIMIT
        # ----------------------------------------------------

        if (
            search_position
            >= HIKVISION_MAX_RESULTS
        ):

            print(
                f"[Channel {channel}] "
                f"Reached Hikvision maximum "
                f"search limit of "
                f"{HIKVISION_MAX_RESULTS}."
            )

            break

    # ========================================================
    # SORT ALL RECORDINGS
    # ========================================================

    recordings.sort(
        key=lambda item: item["start"]
    )

    # ========================================================
    # DEBUG OLDEST / NEWEST
    # ========================================================

    if recordings:

        oldest = recordings[0]["start"]

        newest = recordings[-1]["end"]

        print(
            f"[Channel {channel}] "
            f"Total recordings found: "
            f"{len(recordings)}"
        )

        print(
            f"[Channel {channel}] "
            f"Oldest recording: "
            f"{get_pht_iso_string(oldest)}"
        )

        print(
            f"[Channel {channel}] "
            f"Newest recording: "
            f"{get_pht_iso_string(newest)}"
        )

    else:

        print(
            f"[Channel {channel}] "
            f"No valid recordings found."
        )

    return recordings


# ============================================================
# DAHUA RECORDING SEARCH
# ============================================================

async def get_dahua_recordings(
    client: httpx.AsyncClient,
    channel: int,
    now: datetime
):
    """
    Gets recording segments from Dahua
    for the last 100 days.

    Dahua returns multiple files through
    findNextFile.

    We keep requesting pages until all
    available recording files have been collected.
    """

    hundred_days_ago = (
        now
        - timedelta(days=100)
    )

    start_str = hundred_days_ago.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    end_str = now.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    create_url = (
        f"http://{NVR_IP}"
        f"/cgi-bin/mediaFileFind.cgi"
        f"?action=factory.create"
    )

    res_create = await client.get(
        create_url
    )

    obj_id = parse_dahua_response(
        res_create.text
    ).get("result")

    if not obj_id:

        raise RuntimeError(
            "Failed to create Dahua search session"
        )

    try:

        # ----------------------------------------------------
        # SET SEARCH CONDITIONS
        # ----------------------------------------------------

        cond_url = (
            f"http://{NVR_IP}"
            f"/cgi-bin/mediaFileFind.cgi"
            f"?action=findFile"
            f"&object={obj_id}"
            f"&condition.Channel={channel}"
            f"&condition.StartTime={start_str}"
            f"&condition.EndTime={end_str}"
            f"&condition.Types[0]=dav"
        )

        res_find = await client.get(
            cond_url
        )

        if res_find.status_code != 200:

            raise RuntimeError(
                "Dahua findFile failed with "
                f"HTTP {res_find.status_code}"
            )

        recordings = []

        # Request multiple results per page.

        page_size = 100

        # ----------------------------------------------------
        # GET FILES
        # ----------------------------------------------------

        while True:

            next_url = (
                f"http://{NVR_IP}"
                f"/cgi-bin/mediaFileFind.cgi"
                f"?action=findNextFile"
                f"&object={obj_id}"
                f"&count={page_size}"
            )

            res_next = await client.get(
                next_url
            )

            parsed_next = parse_dahua_response(
                res_next.text
            )

            found = int(
                parsed_next.get(
                    "found",
                    "0"
                ) or "0"
            )

            if found <= 0:

                break

            for index in range(found):

                start_key = (
                    f"items[{index}].StartTime"
                )

                end_key = (
                    f"items[{index}].EndTime"
                )

                start_dt = parse_dahua_time(
                    parsed_next.get(
                        start_key
                    )
                )

                end_dt = parse_dahua_time(
                    parsed_next.get(
                        end_key
                    )
                )

                if (
                    start_dt
                    and end_dt
                    and end_dt >= start_dt
                ):

                    recordings.append({
                        "start": start_dt,
                        "end": end_dt
                    })

            # If fewer records than requested
            # were returned, this is normally
            # the final page.

            if found < page_size:

                break

        return recordings

    finally:

        destroy_url = (
            f"http://{NVR_IP}"
            f"/cgi-bin/mediaFileFind.cgi"
            f"?action=factory.destroy"
            f"&object={obj_id}"
        )

        try:

            await client.get(
                destroy_url
            )

        except Exception:

            pass


# ============================================================
# RETENTION + GAP DETECTION
# ============================================================

async def get_channel_retention(
    client: httpx.AsyncClient,
    channel: int,
    brand: str
):
    """
    Calculates retention metadata and detects
    all significant recording gaps.
    """

    now = datetime.now(PHT)

    try:

        # ====================================================
        # HIKVISION
        # ====================================================

        if brand == "HIKVISION":

            # Hikvision CMSearch timestamps are UTC.

            utc_now = datetime.now(
                timezone.utc
            )

            recordings = (
                await get_hikvision_recordings(
                    client,
                    channel,
                    utc_now
                )
            )

            # ------------------------------------------------
            # NO RECORDINGS
            # ------------------------------------------------

            if not recordings:

                return {
                    "storeName": STORE_NAME,
                    "hasRecording": False,
                    "retentionDays": 0,
                    "message": "No recording found",
                    "gap": []
                }

            # ------------------------------------------------
            # SORT RECORDINGS
            # ------------------------------------------------

            recordings.sort(
                key=lambda item: item["start"]
            )

            # ------------------------------------------------
            # TRUE OLDEST RECORDING
            # ------------------------------------------------

            oldest_date = recordings[0][
                "start"
            ]

            # ------------------------------------------------
            # RETENTION CALCULATION
            # ------------------------------------------------

            diff_time = (
                utc_now
                - oldest_date
            )

            retention_days = math.ceil(
                diff_time.total_seconds()
                / (24 * 3600)
            )

            # ------------------------------------------------
            # GAP DETECTION
            # ------------------------------------------------

            gaps = detect_gaps(
                recordings
            )

            # ------------------------------------------------
            # DEBUG
            # ------------------------------------------------

            print(
                f"[Channel {channel}] "
                f"FINAL RETENTION: "
                f"{retention_days} days"
            )

            print(
                f"[Channel {channel}] "
                f"OLDEST: "
                f"{get_pht_iso_string(oldest_date)}"
            )

            print(
                f"[Channel {channel}] "
                f"GAPS FOUND: "
                f"{len(gaps)}"
            )

            # ------------------------------------------------
            # RETURN DATA
            # ------------------------------------------------

            return {
                "storeName": STORE_NAME,

                "hasRecording": True,

                "oldestRecording":
                    get_pht_iso_string(
                        oldest_date
                    ),

                # Keep current behavior:
                # latestRecording represents current time.

                "latestRecording":
                    get_pht_iso_string(
                        now
                    ),

                "retentionDays":
                    retention_days,

                "gap":
                    gaps
            }


        # ====================================================
        # DAHUA
        # ====================================================

        elif brand == "DAHUA":

            recordings = (
                await get_dahua_recordings(
                    client,
                    channel,
                    now
                )
            )

            # ------------------------------------------------
            # NO RECORDINGS
            # ------------------------------------------------

            if not recordings:

                return {
                    "storeName": STORE_NAME,
                    "hasRecording": False,
                    "retentionDays": 0,
                    "message": "No recording found",
                    "gap": []
                }

            # ------------------------------------------------
            # SORT RECORDINGS
            # ------------------------------------------------

            recordings.sort(
                key=lambda item: item["start"]
            )

            # ------------------------------------------------
            # TRUE OLDEST RECORDING
            # ------------------------------------------------

            oldest_date = recordings[0][
                "start"
            ]

            # ------------------------------------------------
            # RETENTION CALCULATION
            # ------------------------------------------------

            diff_time = (
                now
                - oldest_date
            )

            retention_days = math.ceil(
                diff_time.total_seconds()
                / (24 * 3600)
            )

            # ------------------------------------------------
            # GAP DETECTION
            # ------------------------------------------------

            gaps = detect_gaps(
                recordings
            )

            # ------------------------------------------------
            # DEBUG
            # ------------------------------------------------

            print(
                f"[Channel {channel}] "
                f"FINAL RETENTION: "
                f"{retention_days} days"
            )

            print(
                f"[Channel {channel}] "
                f"OLDEST: "
                f"{get_pht_iso_string(oldest_date)}"
            )

            print(
                f"[Channel {channel}] "
                f"GAPS FOUND: "
                f"{len(gaps)}"
            )

            # ------------------------------------------------
            # RETURN DATA
            # ------------------------------------------------

            return {
                "storeName": STORE_NAME,

                "hasRecording": True,

                "oldestRecording":
                    get_pht_iso_string(
                        oldest_date
                    ),

                "latestRecording":
                    get_pht_iso_string(
                        now
                    ),

                "retentionDays":
                    retention_days,

                "gap":
                    gaps
            }


        # ====================================================
        # UNKNOWN BRAND
        # ====================================================

        return {
            "storeName": STORE_NAME,
            "hasRecording": False,
            "retentionDays": 0,
            "message": "Unknown NVR brand",
            "gap": []
        }


    # ========================================================
    # ERROR HANDLING
    # ========================================================

    except Exception as e:

        print(
            f"[Channel {channel}] "
            f"Retention error: {str(e)}"
        )

        return {
            "storeName": STORE_NAME,
            "hasRecording": False,
            "retentionDays": 0,
            "gap": [],
            "status":
                "Failed parsing retention details",
            "error":
                str(e)
        }


# ============================================================
# MAIN EXECUTION
# ============================================================

async def generate_collage_and_retention():

    # Both Hikvision and Dahua use Digest Auth.

    auth = httpx.DigestAuth(
        USERNAME,
        PASSWORD
    )

    pht_now = datetime.now(PHT)

    pht_date_stamp = pht_now.strftime(
        "%Y-%m-%d"
    )

    async with httpx.AsyncClient(
        auth=auth,
        timeout=30.0
    ) as client:

        # ----------------------------------------------------
        # DETECT BRAND
        # ----------------------------------------------------

        brand = await detect_nvr_brand(
            client
        )

        if brand == "UNKNOWN":

            print(
                f"❌ Error: Could not determine "
                f"NVR brand for {NVR_IP}. "
                f"Check credentials or IP."
            )

            return

        print(
            f"✅ Detected {brand} NVR!"
        )

        print(
            f"Processing feeds, retention, "
            f"and recording gaps for "
            f"{len(CHANNELS)} channels..."
        )

        # ----------------------------------------------------
        # CREATE TASKS
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # RUN ALL CHANNELS
        # ----------------------------------------------------

        results = await asyncio.gather(
            *tasks
        )

    # ========================================================
    # PROCESS RESULTS
    # ========================================================

    valid_snapshots = []

    retention_data = {}

    for i, (
        snapshot,
        retention
    ) in enumerate(results):

        channel = CHANNELS[i]

        if retention.get(
            "hasRecording"
        ) is True:

            retention_data[
                f"channel_{channel}"
            ] = retention

        if snapshot is not None:

            valid_snapshots.append(
                snapshot
            )

    # ========================================================
    # SAVE RETENTION ONLY
    # ========================================================

    if not valid_snapshots:

        print(
            "⚠️ No valid image feeds found."
        )

        print(
            "💾 Saving retention data only."
        )

        json_path = (
            uploads_dir
            / f"{pht_date_stamp}-"
              f"{brand.lower()}-retention.txt"
        )

        # Delete previous retention files.

        for old_file in uploads_dir.glob(
            f"*-{brand.lower()}-retention.txt"
        ):

            if old_file != json_path:

                old_file.unlink()

        with open(
            json_path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                retention_data,
                f,
                indent=4,
                ensure_ascii=False
            )

        print(
            f"📁 Retention data saved: "
            f"{json_path}"
        )

        return

    # ========================================================
    # COLLAGE GRID
    # ========================================================

    tile_width = 640

    tile_height = 360

    cols = math.ceil(
        math.sqrt(
            len(valid_snapshots)
        )
    )

    rows = math.ceil(
        len(valid_snapshots)
        / cols
    )

    # ========================================================
    # CREATE COLLAGE
    # ========================================================

    collage = Image.new(
        "RGB",
        (
            cols * tile_width,
            rows * tile_height
        ),
        (0, 0, 0)
    )

    for index, img in enumerate(
        valid_snapshots
    ):

        x = (
            index % cols
        ) * tile_width

        y = (
            index // cols
        ) * tile_height

        collage.paste(
            img,
            (x, y)
        )

    # ========================================================
    # FILE PATHS
    # ========================================================

    image_path = (
        uploads_dir
        / f"{pht_date_stamp}-"
          f"{brand.lower()}-collage.jpg"
    )

    json_path = (
        uploads_dir
        / f"{pht_date_stamp}-"
          f"{brand.lower()}-retention.txt"
    )

    # ========================================================
    # DELETE OLD COLLAGE FILES
    # ========================================================

    for old_file in uploads_dir.glob(
        f"*-{brand.lower()}-collage.jpg"
    ):

        if old_file != image_path:

            old_file.unlink()

    # ========================================================
    # DELETE OLD RETENTION FILES
    # ========================================================

    for old_file in uploads_dir.glob(
        f"*-{brand.lower()}-retention.txt"
    ):

        if old_file != json_path:

            old_file.unlink()

    # ========================================================
    # SAVE COLLAGE
    # ========================================================

    collage.save(
        image_path,
        "JPEG",
        quality=85
    )

    # ========================================================
    # SAVE RETENTION DATA
    # ========================================================

    with open(
        json_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            retention_data,
            f,
            indent=4,
            ensure_ascii=False
        )

    # ========================================================
    # SUCCESS
    # ========================================================

    print(
        "\n✅ Success! Collage and "
        "retention data generated."
    )

    print(
        f"📁 Image Saved: {image_path}"
    )

    print(
        f"📁 JSON Saved: {json_path}\n"
    )


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        generate_collage_and_retention()
    )