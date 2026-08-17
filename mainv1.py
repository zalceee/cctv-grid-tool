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

# --- HELPER FUNCTIONS ---

def get_pht_iso_string(dt: datetime) -> str:
    """Converts a datetime object to a PHT (+08:00) formatted ISO string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PHT).isoformat()

def parse_dahua_response(text: str) -> dict:
    """Parses Dahua's plain text key=value response into a Python dictionary."""
    result = {}
    for line in text.strip().split('\n'):
        if '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip()
    return result

# --- BRAND DETECTION ---

async def detect_nvr_brand(client: httpx.AsyncClient) -> str:
    """Probes the NVR to automatically detect if it is Hikvision or Dahua."""
    print(f"Probing {NVR_IP} to detect NVR brand...")
    
    # 1. Probe for Hikvision (ISAPI)
    try:
        res_hik = await client.get(f"http://{NVR_IP}/ISAPI/System/deviceInfo")
        if res_hik.status_code == 200:
            return "HIKVISION"
    except Exception:
        pass

    # 2. Probe for Dahua (CGI)
    try:
        res_dah = await client.get(f"http://{NVR_IP}/cgi-bin/magicBox.cgi?action=getSystemInfo")
        if res_dah.status_code == 200:
            return "DAHUA"
    except Exception:
        pass

    return "UNKNOWN"

# --- UNIVERSAL DATA FETCHERS ---

async def get_channel_snapshot(client: httpx.AsyncClient, channel: int, brand: str):
    """Captures and resizes a JPEG snapshot based on the detected brand."""
    if brand == "HIKVISION":
        url = f"http://{NVR_IP}/ISAPI/Streaming/channels/{channel}/picture"
    else: # DAHUA
        url = f"http://{NVR_IP}/cgi-bin/snapshot.cgi?channel={channel}"
        
    try:
        response = await client.get(url)
        if response.status_code != 200:
            print(f"[Channel {channel}] Snapshot fetch failed with status {response.status_code}")
            return None
        
        img = Image.open(BytesIO(response.content))
        return img.resize((640, 360), Image.Resampling.LANCZOS)
    except Exception as e:
        print(f"[Channel {channel}] Snapshot error: {str(e)}")
        return None

async def get_channel_retention(client: httpx.AsyncClient, channel: int, brand: str):
    """Calculates true retention metadata based on the detected brand's search engine."""
    now = datetime.now(PHT)
    hundred_days_ago = now - timedelta(days=100)

    try:
        if brand == "HIKVISION":
            # Hikvision requires strict UTC format (Z) for searches
            utc_now = datetime.now(timezone.utc)
            utc_hundred_ago = utc_now - timedelta(days=100)
            start_time = utc_hundred_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_time = utc_now.strftime("%Y-%m-%dT%H:%M:%SZ")
            unique_search_id = str(uuid.uuid4())

            xml_payload = f"""
            <CMSearchDescription>
                <searchID>{unique_search_id}</searchID>
                <trackList><trackID>{channel}</trackID></trackList>
                <timeSpanList>
                    <timeSpan><startTime>{start_time}</startTime><endTime>{end_time}</endTime></timeSpan>
                </timeSpanList>
                <maxResults>1</maxResults>
            </CMSearchDescription>
            """.strip()

            url = f"http://{NVR_IP}/ISAPI/ContentMgmt/search"
            response = await client.post(url, content=xml_payload, headers={"Content-Type": "application/xml"})
            
            if response.status_code != 200:
                return {"status": "Error querying recordings"}

            result = xmltodict.parse(response.text)
            match_list = result.get("CMSearchResult", {}).get("matchList", {})
            if not match_list:
                return {"hasRecording": False, "retentionDays": 0, "message": "No recording found"}

            match_items = match_list.get("searchMatchItem")
            if not match_items:
                return {"hasRecording": False, "retentionDays": 0, "message": "No recording found"}

            first_match = match_items[0] if isinstance(match_items, list) else match_items
            start_time_str = first_match.get("timeSpan", {}).get("startTime")
            
            oldest_date = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            diff_time = utc_now - oldest_date
            
            return {
                "storeName": STORE_NAME,
                "hasRecording": True,
                "oldestRecording": get_pht_iso_string(oldest_date),
                "latestRecording": get_pht_iso_string(now),
                "retentionDays": math.ceil(diff_time.total_seconds() / (24 * 3600))
            }

        elif brand == "DAHUA":
            start_str = hundred_days_ago.strftime("%Y-%m-%d %H:%M:%S")
            end_str = now.strftime("%Y-%m-%d %H:%M:%S")

            create_url = f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi?action=factory.create"
            res_create = await client.get(create_url)
            obj_id = parse_dahua_response(res_create.text).get("result")
            
            if not obj_id:
                return {"hasRecording": False, "retentionDays": 0, "message": "Failed to create search session"}

            cond_url = (f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi?action=findFile"
                        f"&object={obj_id}&condition.Channel={channel}"
                        f"&condition.StartTime={start_str}&condition.EndTime={end_str}"
                        f"&condition.Types[0]=dav")
            await client.get(cond_url)

            next_url = f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi?action=findNextFile&object={obj_id}&count=1"
            res_next = await client.get(next_url)
            parsed_next = parse_dahua_response(res_next.text)

            destroy_url = f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi?action=factory.destroy&object={obj_id}"
            await client.get(destroy_url)

            found = parsed_next.get("found", "0")
            if found == "0" or "items[0].StartTime" not in parsed_next:
                return {"hasRecording": False, "retentionDays": 0, "message": "No recording found"}

            oldest_str = parsed_next["items[0].StartTime"]
            oldest_date = datetime.strptime(oldest_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=PHT)
            
            diff_time = now - oldest_date
            
            return {
                "storeName": STORE_NAME,
                "hasRecording": True,
                "oldestRecording": get_pht_iso_string(oldest_date),
                "latestRecording": get_pht_iso_string(now),
                "retentionDays": math.ceil(diff_time.total_seconds() / (24 * 3600))
            }
            
    except Exception as e:
        return {"status": "Failed parsing retention details", "error": str(e)}

# --- MAIN EXECUTION LOGIC ---

async def generate_collage_and_retention():
    # Both Hikvision and Dahua use Digest Auth for secure API access
    auth = httpx.DigestAuth(USERNAME, PASSWORD)
    
    pht_now = datetime.now(PHT)
    pht_date_stamp = pht_now.strftime("%Y-%m-%d")

    async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
        # Detect the brand before doing anything else
        brand = await detect_nvr_brand(client)
        
        if brand == "UNKNOWN":
            print(f"❌ Error: Could not determine NVR brand for {NVR_IP}. Check credentials or IP.")
            return
            
        print(f"✅ Detected {brand} NVR!")
        print(f"Processing feeds and calculating real retention for {len(CHANNELS)} channels...")

        # Create concurrent tasks based on the detected brand
        tasks = []
        for channel in CHANNELS:
            task = asyncio.gather(
                get_channel_snapshot(client, channel, brand),
                get_channel_retention(client, channel, brand)
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks)

    valid_snapshots = []
    retention_data = {}

    for i, (snapshot, retention) in enumerate(results):
        channel = CHANNELS[i]
        # retention_data[f"channel_{channel}"] = retention
        # if snapshot is not None:
        #     valid_snapshots.append(snapshot)
        if retention.get("hasRecording") is True:
            retention_data[f"channel_{channel}"] = retention
            
        if snapshot is not None:
            valid_snapshots.append(snapshot)

    if not valid_snapshots:
        print("⚠️ No valid image feeds found.")
        print("💾 Saving retention data only.")

        json_path = uploads_dir / f"{pht_date_stamp}-{brand.lower()}-retention.txt"

        with open(json_path, "w") as f:
            json.dump(retention_data, f, indent=4)

        print(f"📁 Retention data saved: {json_path}")
        return

    # Dynamic Canvas Grid Math
    tile_width = 640
    tile_height = 360
    cols = math.ceil(math.sqrt(len(valid_snapshots)))
    rows = math.ceil(len(valid_snapshots) / cols)

    # Generate final composite collage using Pillow
    collage = Image.new("RGB", (cols * tile_width, rows * tile_height), (0, 0, 0))

    for index, img in enumerate(valid_snapshots):
        x = (index % cols) * tile_width
        y = (index // cols) * tile_height
        collage.paste(img, (x, y))

    # Dynamic file naming based on the brand
    image_path = uploads_dir / f"{pht_date_stamp}-{brand.lower()}-collage.jpg"
    json_path = uploads_dir / f"{pht_date_stamp}-{brand.lower()}-retention.txt"


    # Delete previous day's files for this brand
    for old_file in uploads_dir.glob(f"*-{brand.lower()}-collage.jpg"):
        if old_file != image_path:
            old_file.unlink()

    for old_file in uploads_dir.glob(f"*-{brand.lower()}-retention.txt"):
        if old_file != json_path:
            old_file.unlink()

    # Save JPEG with 85% quality
    collage.save(image_path, "JPEG", quality=85)
    
    with open(json_path, "w") as f:
        json.dump(retention_data, f, indent=4)

    print("\n✅ Success! Collage and retention data generated.")
    print(f"📁 Image Saved: {image_path}")
    print(f"📁 JSON Saved: {json_path}\n")

if __name__ == "__main__":
    asyncio.run(generate_collage_and_retention())

    