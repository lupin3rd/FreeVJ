import datetime
import os
import re
import subprocess
import threading
import time
from urllib.parse import unquote, urlparse
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

# Listening ports configuration
LISTEN_IP = "0.0.0.0"       # Listen on all available network interfaces
LISTEN_PORT = 6666          # Main input port (forwards non-vimixplus messages)
FROMVIMIX_PORT = 6667       # Secondary input port from Vimix
REPLY_PORT = 6668           # Dedicated output port for vimixplus replies (blobs, info)

# Forward destination configuration
TOVIMIX_IP = "127.0.0.1"
TOVIMIX_PORT = 7000

# Logging Verbosity Level:
# 0 = RESTRICTIVE : Prints ONLY errors, system events, showdata table, and Vimix resets.
# 1 = CHANGES     : Prints state changes, info registrations, and thumbnail transfers.
# 2 = ALL/VERBOSE : Prints ALL incoming and outgoing OSC messages across all 4 ports.
LOG_LEVEL = 1

# Sync Loops Configuration (in milliseconds)
sync_interval_time = 2000    # Interval in ms for /vimix/current/sync requests (default 2000ms = 2s)
info_requests_time = 500     # Polling interval in ms for /vimixplus/info/ requests

# Ordered list of all official Vimix properties
ALL_PROPERTIES = [
    "index", "name", "lock", "failed", "play", "pause", "blending", "alpha",
    "transparency", "depth", "position", "size", "corner", "angle",
    "seek", "speed", "brightness", "contrast", "saturation", "hue",
    "threshold", "gamma", "color", "posterize", "invert", "uri"
]

SUPPORTED_PROPERTIES = set(ALL_PROPERTIES)

# ANSI Escape Codes for colored terminal output
COLOR_RESET = "\033[0m"
COLOR_LOCAL = "\033[92m"    # Green for local vimixplus messages
COLOR_FORWARD = "\033[93m"  # Yellow for forwarded messages
COLOR_VIMIX = "\033[94m"    # Blue for parsed /vimix/ index data
COLOR_THUMB = "\033[96m"    # Cyan for thumbnail operations
COLOR_RESET_EV = "\033[91m" # Red for reset events
COLOR_OTHER = "\033[90m"    # Gray for non-forwarded messages
COLOR_TIMESTAMP = "\033[90m"# Gray for timestamp
COLOR_CLIENT = "\033[96m"   # Cyan for client info
COLOR_PORT = "\033[95m"     # Magenta for server listening port
COLOR_TABLE = "\033[97m"    # Bright white for table headers

# OSC client used to forward messages and send sync commands to port 7000
forward_client = SimpleUDPClient(TOVIMIX_IP, TOVIMIX_PORT)

# OSC client used to send local replies back to REPLY_PORT (6668)
local_reply_client = SimpleUDPClient("127.0.0.1", REPLY_PORT)

# Global State Variables
vimix_data = {}         # Structure: { idx: { "name": ..., "uri": ..., "thumbnails": [b'...'] } }
source_current = None   # Stores currently selected source index in Vimix (int)
selector_source = 0     # Local selector index (int)
info_requests = {}      # Structure: { "nomeinfo": { "nomemedia": "...", "properties": [...] } }

def format_args(args):
    """Formats arguments for clean display."""
    if not args:
        return "<no arguments>"
    return ", ".join(repr(arg) for arg in args)

def create_empty_vimix_entry():
    """Returns a dictionary with all supported Vimix properties initialized to None and an empty thumbnails list."""
    entry = {prop: None for prop in SUPPORTED_PROPERTIES}
    entry["thumbnails"] = []
    return entry

def find_index_by_name(name_str):
    """Searches vimix_data for the index matching a given name string."""
    for idx, data in vimix_data.items():
        if data.get("name") == name_str:
            return idx
    return None

def clean_uri_path(uri_value):
    """
    Decodes percent-encoded characters (e.g., %20 -> space) and strips file:// 
    to yield an absolute file system path for FFmpeg.
    """
    if not uri_value:
        return ""
    
    uri_str = str(uri_value).strip()
    if uri_str.startswith("file://"):
        raw_path = urlparse(uri_str).path
        clean_path = unquote(raw_path)
    else:
        clean_path = unquote(uri_str)

    return os.path.abspath(clean_path)

# ==============================================================================
# FFMPEG THUMBNAIL GENERATOR (IN-MEMORY / BYTES BUFFER)
# ==============================================================================

def get_video_duration(file_path):
    """Gets video duration in seconds using ffprobe with correct noprint_wrappers option."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip() and line.strip() != "N/A"]
        for line in lines:
            try:
                val = float(line)
                if val > 0:
                    return val
            except ValueError:
                continue
        return 0.0
    except Exception:
        return 0.0

def extract_single_frame(file_path, timestamp_sec, width=320, height=180):
    """
    Extracts 1 frame at exact timestamp_sec directly to stdout as JPEG bytes.
    Uses accurate seeking (-ss before -i with -accurate_seek).
    """
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{timestamp_sec:.2f}",
        "-accurate_seek",
        "-i", file_path,
        "-vframes", "1",
        "-s", f"{width}x{height}",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-"  # Output to stdout
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0 and len(result.stdout) > 100:
            return result.stdout
        return None
    except Exception as e:
        print(f"{COLOR_RESET_EV}[THUMBNAIL ERROR]{COLOR_RESET} Failed to extract frame at {timestamp_sec}s: {e}")
        return None

def extract_thumbnails_from_file(file_path, count=5, width=320, height=180):
    """Extracts 'count' thumbnails for video or 1 frame for static images."""
    duration = get_video_duration(file_path)
    thumbnails = []

    # If duration is 0 or unavailable, attempt as static image
    if duration <= 0:
        frame = extract_single_frame(file_path, timestamp_sec=0, width=width, height=height)
        if frame:
            thumbnails.append(frame)
        return thumbnails

    # Calculate 5 incremental timestamps (e.g. 10%, 30%, 50%, 70%, 90% of duration)
    step = duration / (count + 1)
    for i in range(1, count + 1):
        target_time = step * i
        frame = extract_single_frame(file_path, timestamp_sec=target_time, width=width, height=height)
        if frame:
            thumbnails.append(frame)

    return thumbnails

def generate_thumbnails_worker(idx, uri_value):
    """Worker process to extract 5 thumbnails from URI into RAM."""
    file_path = clean_uri_path(uri_value)

    if not os.path.exists(file_path):
        print(f"{COLOR_RESET_EV}[THUMBNAIL]{COLOR_RESET} Index {idx}: File not found -> '{file_path}'")
        if idx in vimix_data:
            vimix_data[idx]["thumbnails"] = []
        return

    timestamp_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    if LOG_LEVEL >= 1:
        print(
            f"{COLOR_TIMESTAMP}[{timestamp_str}]{COLOR_RESET} "
            f"{COLOR_THUMB}│ THUMB PROCESSING │{COLOR_RESET} "
            f"Extracting thumbnails for Index {idx} ({os.path.basename(file_path)})..."
        )

    # Generate thumbnails array in RAM
    thumbnails = extract_thumbnails_from_file(file_path, count=5)

    # Store thumbnails in RAM
    if idx in vimix_data:
        vimix_data[idx]["thumbnails"] = thumbnails
        timestamp_done = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if LOG_LEVEL >= 1:
            print(
                f"{COLOR_TIMESTAMP}[{timestamp_done}]{COLOR_RESET} "
                f"{COLOR_THUMB}│ THUMB READY      │{COLOR_RESET} "
                f"Index {idx}: Generated {len(thumbnails)} thumbnail(s) in RAM for '{os.path.basename(file_path)}'"
            )

# ==============================================================================
# ROUTING & OSC HANDLERS
# ==============================================================================

def process_selector_command(args):
    """
    Handles cyclic selector navigation over contiguous indices in vimix_data.
    Supported arguments: 'next', 'prev', 'current' (default).
    Forces URI sync and generates thumbnails synchronously if missing before sending reply message /vimixplus/replyselector.
    """
    global selector_source

    action = str(args[0]).lower().strip() if args else "current"
    total_sources = len(vimix_data)

    if total_sources == 0:
        if LOG_LEVEL >= 1:
            print(f"{COLOR_LOCAL}│ SELECTOR │{COLOR_RESET} vimix_data is empty. selector_source remains at {selector_source}")
        return

    if action == "next":
        selector_source = (selector_source + 1) % total_sources
    elif action == "prev":
        selector_source = (selector_source - 1 + total_sources) % total_sources
    elif action == "current":
        # Keep current value, ensuring it stays within valid bounds
        selector_source = selector_source % total_sources
    else:
        print(f"{COLOR_RESET_EV}[SELECTOR ERROR]{COLOR_RESET} Invalid action '{action}'. Expected 'next', 'prev', or 'current'.")
        return

    # Extract target entry data
    current_entry = vimix_data.get(selector_source, {})
    media_name = str(current_entry.get("name") if current_entry.get("name") is not None else "<unnamed>")
    uri_val = current_entry.get("uri")

    # 1. Se l'URI non è ancora presente in vimix_data, forziamo la richiesta a Vimix ed attendiamo la risposta
    if not uri_val:
        if LOG_LEVEL >= 1:
            print(f"{COLOR_LOCAL}│ SELECTOR │{COLOR_RESET} URI mancante per Index {selector_source}. Forzatura sync URI in corso...")
        forward_client.send_message(f"/vimix/{selector_source}/get", ["uri"])
        
        # Attesa sincrona (fino a 500ms) affinché il listener riceva l'URI da Vimix
        for _ in range(10):
            time.sleep(0.05)
            uri_val = vimix_data.get(selector_source, {}).get("uri")
            if uri_val:
                break

    # 2. Se le miniature mancano e ora abbiamo l'URI, le generiamo in modo sincrono prima di rispondere
    thumbnails = current_entry.get("thumbnails", [])
    if not thumbnails and uri_val:
        if LOG_LEVEL >= 1:
            print(f"{COLOR_LOCAL}│ SELECTOR │{COLOR_RESET} Miniature mancanti per Index {selector_source}. Generazione sincrona...")
        generate_thumbnails_worker(selector_source, uri_val)
        thumbnails = vimix_data.get(selector_source, {}).get("thumbnails", [])

    # 3. Protezione da pythonosc.BuildError: evita di passare b"" (lunghezza 0) se la miniatura non è disponibile
    if len(thumbnails) > 0 and len(thumbnails[0]) > 0:
        thumb0 = thumbnails[0]
    else:
        thumb0 = b"\x00"  # Byte non vuoto dummy per prevenire l'errore del builder OSC

    # Send OSC reply to port 6668
    local_reply_client.send_message("/vimixplus/replyselector", [media_name, selector_source, thumb0])

    if LOG_LEVEL >= 1:
        print(
            f"{COLOR_LOCAL}│ SELECTOR UPDATED│{COLOR_RESET} "
            f"Action: '{action}' -> selector_source: {selector_source} ('{media_name}') "
            f"➔ Sent /vimixplus/replyselector on port {REPLY_PORT}"
        )

def print_vimix_table():
    """Prints a clean tabular view of active Vimix sources, registered info_requests, and selector_source state."""
    print("\n" + "=" * 95)
    print(f"{COLOR_TABLE}                       CURRENT VIMIX STATE TABLE                        {COLOR_RESET}")
    print(f"                       Selected Source (source-current): {source_current}")
    print("=" * 95)

    if not vimix_data:
        print("  [No Vimix data received yet]")
    else:
        # Header
        print(f"{COLOR_TABLE}{'INDEX':<7} | {'NAME':<18} | {'THUMBS':<8} | {'ACTIVE PROPERTIES & VALUES'}{COLOR_RESET}")
        print("-" * 95)

        # Rows sorted by Index
        for idx in sorted(vimix_data.keys()):
            entry = vimix_data[idx]
            name = str(entry.get("name") if entry.get("name") is not None else "<unnamed>")
            num_thumbs = len(entry.get("thumbnails", []))

            # Format active properties (ignoring None and thumbnails)
            active_props = [
                f"{k}: {repr(v)}" for k, v in sorted(entry.items()) 
                if v is not None and k not in ("name", "thumbnails")
            ]
            props_str = ", ".join(active_props) if active_props else "<no other properties set>"

            print(f"{COLOR_LOCAL}{idx:<7}{COLOR_RESET} | {COLOR_CLIENT}{name:<18}{COLOR_RESET} | {num_thumbs:<8} | {props_str}")

    print("=" * 95)
    print(f"{COLOR_TABLE}                       REGISTERED INFO REQUESTS (POLLING)               {COLOR_RESET}")
    print("=" * 95)

    if not info_requests:
        print("  [No active info_requests registered]")
    else:
        print(f"{COLOR_TABLE}{'NOMEINFO':<20} | {'NOMEMEDIA':<20} | {'MONITORED PROPERTIES'}{COLOR_RESET}")
        print("-" * 95)
        for nomeinfo, data in sorted(info_requests.items()):
            nomemedia = data.get("nomemedia", "<unknown>")
            props = data.get("properties", [])
            props_str = ", ".join(props) if props else "<none>"
            print(f"{COLOR_LOCAL}{nomeinfo:<20}{COLOR_RESET} | {COLOR_CLIENT}{nomemedia:<20}{COLOR_RESET} | {props_str}")

    print("=" * 95)
    selected_name = vimix_data.get(selector_source, {}).get("name", "<unnamed>") if vimix_data else "<no data>"
    print(f"{COLOR_TABLE}  LOCAL SELECTOR STATE (selector_source) : {COLOR_LOCAL}{selector_source}{COLOR_RESET} ({COLOR_CLIENT}{selected_name}{COLOR_RESET})")
    print("=" * 95 + "\n")

def send_thumbnail_blob(client_address, target_identifier, thumb_arg):
    """
    Sends requested thumbnail(s) as OSC Blob back to the client on REPLY_PORT (6668).
    Target path format: /vimixplus/replythumb/<target>/<thumb_idx>
    """
    client_ip, _ = client_address
    reply_client = SimpleUDPClient(client_ip, REPLY_PORT)

    # Determine index
    if str(target_identifier).isdigit():
        idx = int(target_identifier)
    else:
        idx = find_index_by_name(str(target_identifier))

    if idx is None or idx not in vimix_data:
        print(f"{COLOR_RESET_EV}[THUMB ERROR]{COLOR_RESET} Target '{target_identifier}' not found in vimix_data.")
        return

    thumbnails = vimix_data[idx].get("thumbnails", [])
    if not thumbnails:
        print(f"{COLOR_RESET_EV}[THUMB ERROR]{COLOR_RESET} No thumbnails available for Index {idx}.")
        return

    # Determine which thumbnail(s) to send
    arg_str = str(thumb_arg).lower().strip()
    
    if arg_str == "all":
        indices_to_send = list(range(len(thumbnails)))
    elif arg_str.isdigit() and 0 <= int(arg_str) < len(thumbnails):
        indices_to_send = [int(arg_str)]
    else:
        print(f"{COLOR_RESET_EV}[THUMB ERROR]{COLOR_RESET} Invalid thumbnail argument '{thumb_arg}'. Expected 0-4 or 'all'.")
        return

    # Send OSC Blobs back to the client using /vimixplus/replythumb/<target>/<t_idx> on REPLY_PORT (6668)
    for t_idx in indices_to_send:
        blob_data = thumbnails[t_idx]
        osc_reply_path = f"/vimixplus/replythumb/{target_identifier}/{t_idx}"
        reply_client.send_message(osc_reply_path, blob_data)

    if LOG_LEVEL >= 1:
        print(
            f"{COLOR_THUMB}➔ SENT BLOB(S)    │{COLOR_RESET} Sent {len(indices_to_send)} thumbnail blob(s) "
            f"for '{target_identifier}' to {client_ip}:{REPLY_PORT} -> Path: /vimixplus/replythumb/{target_identifier}/..."
        )

def process_dynamic_sync_request(clean_address, args):
    """
    Extracts the target (index or name) from /vimixplus/sync/<target>
    and sends /vimix/<target>/get to port TOVIMIX_PORT with either all properties or requested ones.
    """
    target = clean_address.replace("vimixplus/sync/", "", 1)
    forward_path = f"/vimix/{target}/get"

    req_args = [str(arg) for arg in args]

    if not req_args or "all" in [a.lower() for a in req_args]:
        props_to_send = ALL_PROPERTIES
    else:
        props_to_send = req_args

    forward_client.send_message(forward_path, props_to_send)
    return forward_path, props_to_send

def process_info_registration(clean_address, args):
    """
    Registers or updates a periodic polling request under 'nomeinfo'.
    Validates that 'nomemedia' exists in vimix_data first.
    """
    parts = clean_address.split('/')
    if len(parts) < 4:
        print(f"{COLOR_RESET_EV}[INFO ERROR]{COLOR_RESET} Invalid path format. Expected /vimixplus/info/<nomeinfo>/<nomemedia>")
        return

    nomeinfo = parts[2]
    nomemedia = parts[3]

    # Check if nomemedia exists in vimix_data
    if find_index_by_name(nomemedia) is None:
        print(f"{COLOR_RESET_EV}[INFO ERROR]{COLOR_RESET} Media '{nomemedia}' not found in vimix_data. Request rejected.")
        return

    req_args = [str(a) for a in args]
    if not req_args or "all" in [a.lower() for a in req_args]:
        props = ALL_PROPERTIES
    else:
        props = [p for p in req_args if p in SUPPORTED_PROPERTIES]

    if not props:
        print(f"{COLOR_RESET_EV}[INFO ERROR]{COLOR_RESET} No valid properties specified.")
        return

    info_requests[nomeinfo] = {
        "nomemedia": nomemedia,
        "properties": props
    }

    if LOG_LEVEL >= 1:
        print(
            f"{COLOR_LOCAL}│ INFO REGISTERED│{COLOR_RESET} "
            f"NomeInfo: '{nomeinfo}' -> Media: '{nomemedia}' | Props ({len(props)}): [{format_args(props)}] | Polling: {info_requests_time}ms"
        )

def check_and_send_info_replies(idx, prop_name, new_val):
    """
    Checks if an updated property belongs to a media monitored by any 'nomeinfo' key.
    If so, sends a message to REPLY_PORT (6668): /vimixplus/replyinfo/<nomeinfo> <prop_name> <new_val>
    """
    media_name = vimix_data.get(idx, {}).get("name")
    if not media_name:
        return

    timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    for nomeinfo, data in info_requests.items():
        if data["nomemedia"] == media_name and prop_name in data["properties"]:
            reply_path = f"/vimixplus/replyinfo/{nomeinfo}"
            local_reply_client.send_message(reply_path, [prop_name, new_val])
            
            if LOG_LEVEL >= 2:
                print(
                    f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                    f"{COLOR_PORT}[OUT PORT {REPLY_PORT}]{COLOR_RESET} "
                    f"{COLOR_THUMB}➔ REPLY INFO     │{COLOR_RESET} "
                    f"Path: {reply_path:<25} | Args: [{repr(prop_name)}, {repr(new_val)}]"
                )

def process_vimix_current_message(address, args):
    """
    Parses paths like /vimix/current/<index> <val>.
    If <val> is 1, updates global variable `source_current` with <index>.
    Returns (is_valid, idx, is_changed) tuple.
    """
    global source_current
    pattern = r"^/?vimix/current/(\d+)$"
    match = re.match(pattern, address)

    if match:
        idx = int(match.group(1))
        val = args[0] if args else None
        
        # Check if argument is 1
        if val in (1, 1.0, "1"):
            if source_current != idx:
                source_current = idx
                return True, idx, True  # Updated to new index
            return True, idx, False     # Unchanged

    return False, None, False

def process_vimix_message(address, args):
    """
    Parses paths like /vimix/<index_or_name>/<property> and updates vimix_data.
    Accepts both numeric indices (e.g. /vimix/0/alpha) and media names (e.g. /vimix/camera_1/alpha).
    Triggers thumbnail generation ONLY WHEN 'uri' VALUE ACTUALLY CHANGES.
    Returns (idx, prop_name, val, is_changed) tuple.
    """
    pattern = r"^/?vimix/([^/]+)/(.+)$"
    match = re.match(pattern, address)

    if match:
        target_str = match.group(1)
        prop_name = match.group(2)

        if prop_name not in SUPPORTED_PROPERTIES:
            return None

        # Determine whether target is a numeric index or a media name
        if target_str.isdigit():
            idx = int(target_str)
        else:
            idx = find_index_by_name(target_str)
            # If name is not yet registered in vimix_data, we cannot map it to an integer index
            if idx is None:
                return None

        # Extract value
        if len(args) == 1:
            val = args[0]
        elif len(args) > 1:
            val = list(args)
        else:
            val = None

        if idx not in vimix_data:
            vimix_data[idx] = create_empty_vimix_entry()

        # Reset logic: check if 'name' has changed for this index
        if prop_name == "name":
            current_name = vimix_data[idx].get("name")
            if current_name is not None and current_name != val:
                print(
                    f"{COLOR_RESET_EV}[VIMIX RESET]{COLOR_RESET} "
                    f"Index {idx}: Name changed ('{current_name}' -> '{val}'). Resetting all properties to None."
                )
                vimix_data[idx] = create_empty_vimix_entry()

        current_val = vimix_data[idx].get(prop_name)
        is_changed = (current_val != val)

        # Update vimix_data state
        vimix_data[idx][prop_name] = val

        # TRIGGER THUMBNAIL GENERATION ONLY IF URI VALUE ACTUALLY CHANGED
        if prop_name == "uri" and is_changed and val:
            threading.Thread(
                target=generate_thumbnails_worker, 
                args=(idx, val), 
                daemon=True
            ).start()

        return idx, prop_name, val, is_changed

    return None

def create_osc_handler(server_port):
    """
    Returns an OSC handler function configured for a specific server port.
    """
    def osc_handler(client_address, address, *args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        client_ip, client_port = client_address
        client_id = f"{client_ip}:{client_port}"

        clean_address = address.lstrip('/')

        # 1. Command: /vimixplus/showdata (ALWAYS ACTIVE AT LOG_LEVEL >= 0)
        if clean_address == "vimixplus/showdata":
            if LOG_LEVEL >= 1:
                print(
                    f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                    f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                    f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                    f"{COLOR_LOCAL}│ SHOW TABLE │{COLOR_RESET} Printing Vimix table..."
                )
            print_vimix_table()

        # 2. Command: /vimixplus/selector [prev|next|current]
        elif clean_address == "vimixplus/selector":
            process_selector_command(args)

        # 3. Thumbnail Request Command: /vimixplus/thumb/<index_or_name> <0..4|all>
        elif clean_address.startswith("vimixplus/thumb/"):
            target_identifier = clean_address.replace("vimixplus/thumb/", "", 1)
            thumb_arg = args[0] if args else "all"
            
            if LOG_LEVEL >= 1:
                print(
                    f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                    f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                    f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                    f"{COLOR_THUMB}│ REQ THUMB  │{COLOR_RESET} Target: '{target_identifier}' | Thumb Arg: {thumb_arg}"
                )
            send_thumbnail_blob(client_address, target_identifier, thumb_arg)

        # 4. Dynamic Sync Command: /vimixplus/sync/<target> (e.g. /vimixplus/sync/pippo)
        elif clean_address.startswith("vimixplus/sync/"):
            fwd_path, sent_props = process_dynamic_sync_request(clean_address, args)
            if LOG_LEVEL >= 1:
                print(
                    f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                    f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                    f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                    f"{COLOR_FORWARD}➔ FORWARD GET│{COLOR_RESET} "
                    f"Path: {fwd_path:<20} | Args ({len(sent_props)}): [{format_args(sent_props)}]"
                )

        # 5. Info Polling Registration: /vimixplus/info/<nomeinfo>/<nomemedia>
        elif clean_address.startswith("vimixplus/info/"):
            process_info_registration(clean_address, args)

        # 6. Other messages starting with 'vimixplus'
        elif clean_address.startswith("vimixplus"):
            if LOG_LEVEL >= 2:
                print(
                    f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                    f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                    f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                    f"{COLOR_LOCAL}│ LOCAL MATCH  │{COLOR_RESET} "
                    f"Path: {address:<20} | Args: [{format_args(args)}]"
                )

        # 7. Messages from FROMVIMIX_PORT (6667)
        elif server_port == FROMVIMIX_PORT and clean_address.startswith("vimix/"):
            
            # 7a. Check if path matches /vimix/current/<index> 1
            if clean_address.startswith("vimix/current/"):
                is_valid, idx, is_changed = process_vimix_current_message(address, args)
                if is_valid:
                    if is_changed and LOG_LEVEL >= 1:
                        print(
                            f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                            f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                            f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                            f"{COLOR_VIMIX}│ CURRENT SOURCE│{COLOR_RESET} "
                            f"source_current updated -> {source_current}"
                        )
                    elif not is_changed and LOG_LEVEL >= 2:
                        print(
                            f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                            f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                            f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                            f"{COLOR_OTHER}│ CURRENT UNCHG │{COLOR_RESET} "
                            f"source_current remains -> {source_current}"
                        )

            # 7b. Otherwise, process as property message /vimix/<index_or_name>/<prop>
            else:
                parsed = process_vimix_message(address, args)
                if parsed:
                    idx, prop_name, val, is_changed = parsed
                    
                    # INVIAMO E STAMPIAMO LA RISPOSTA SOLO SE IL VALORE È EFFETTIVAMENTE CAMBIATO
                    if is_changed:
                        if LOG_LEVEL >= 1:
                            active_props = {
                                k: v for k, v in vimix_data[idx].items() 
                                if v is not None and k != "thumbnails"
                            }
                            print(
                                f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                                f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                                f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                                f"{COLOR_VIMIX}│ VIMIX CHANGE │{COLOR_RESET} "
                                f"Index {idx} -> '{prop_name}': {repr(val)} | Active State: {active_props}"
                            )
                        
                        # Risposta inviata esclusivamente quando il valore cambia
                        check_and_send_info_replies(idx, prop_name, val)

                    elif not is_changed and LOG_LEVEL >= 2:
                        print(
                            f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                            f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                            f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                            f"{COLOR_OTHER}│ VIMIX RAW    │{COLOR_RESET} "
                            f"Index {idx} -> '{prop_name}': {repr(val)}"
                        )

        # 8. FORWARD ONLY if message arrived on LISTEN_PORT (6666) AND path is NOT vimixplus
        elif server_port == LISTEN_PORT:
            forward_client.send_message(address, list(args))
            if LOG_LEVEL >= 2:
                print(
                    f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                    f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                    f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                    f"{COLOR_FORWARD}➔ FORWARD {TOVIMIX_PORT} │{COLOR_RESET} "
                    f"Path: {address:<20} | Args: [{format_args(args)}]"
                )

        # 9. Other unhandled messages on FROMVIMIX_PORT (6667)
        else:
            if LOG_LEVEL >= 2:
                print(
                    f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                    f"{COLOR_PORT}[IN PORT {server_port}]{COLOR_RESET} "
                    f"{COLOR_CLIENT}[FROM: {client_id:<21}]{COLOR_RESET} "
                    f"{COLOR_OTHER}│ IGNORED/LOCAL│{COLOR_RESET} "
                    f"Path: {address:<20} | Args: [{format_args(args)}]"
                )

    return osc_handler

def sync_loop():
    """Background worker that sends /vimix/current/sync to port TOVIMIX_PORT at regular intervals."""
    while True:
        try:
            forward_client.send_message("/vimix/current/sync", [])
            if LOG_LEVEL >= 2:
                timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                print(
                    f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                    f"{COLOR_PORT}[OUT PORT {TOVIMIX_PORT}]{COLOR_RESET} "
                    f"{COLOR_FORWARD}➔ AUTO-SYNC     │{COLOR_RESET} Sent /vimix/current/sync"
                )
        except Exception as e:
            print(f"Error sending sync message: {e}")
        
        time.sleep(sync_interval_time / 1000.0)

def info_polling_loop():
    """
    Background worker that sends /vimix/<nomemedia>/get requests to port TOVIMIX_PORT
    every 'info_requests_time' ms for each registered 'nomeinfo' task.
    """
    while True:
        try:
            for nomeinfo, data in list(info_requests.items()):
                nomemedia = data["nomemedia"]
                props = data["properties"]
                
                # Send GET request to Vimix using media name
                forward_path = f"/vimix/{nomemedia}/get"
                forward_client.send_message(forward_path, props)

                if LOG_LEVEL >= 2:
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    print(
                        f"{COLOR_TIMESTAMP}[{timestamp}]{COLOR_RESET} "
                        f"{COLOR_PORT}[OUT PORT {TOVIMIX_PORT}]{COLOR_RESET} "
                        f"{COLOR_FORWARD}➔ INFO POLLING  │{COLOR_RESET} "
                        f"Target: '{nomemedia}' | Props ({len(props)}): [{format_args(props)}]"
                    )
        except Exception as e:
            print(f"Error in info polling loop: {e}")

        time.sleep(info_requests_time / 1000.0)

def start_server_instance(ip, port):
    """Initializes and starts a single OSC server instance on a given port."""
    dispatcher = Dispatcher()
    handler = create_osc_handler(port)
    dispatcher.set_default_handler(handler, needs_reply_address=True)

    server = BlockingOSCUDPServer((ip, port), dispatcher)
    server.serve_forever()

def main():
    print("==========================================================================================")
    print("                      MULTI-PORT OSC ROUTER 'vimixplus' STARTED                           ")
    print("==========================================================================================")
    print(f"• LOG_LEVEL Verbosity         : {LOG_LEVEL} (0=Restrictive, 1=Changes, 2=All/Verbose)")
    print(f"• Listening on LISTEN_PORT    : {LISTEN_PORT} (IP: {LISTEN_IP})")
    print(f"• Listening on FROMVIMIX_PORT : {FROMVIMIX_PORT} (IP: {LISTEN_IP})")
    print(f"• Outgoing REPLY_PORT         : {REPLY_PORT}")
    print(f"• Forwarding rule             : Port {LISTEN_PORT} ONLY (non-vimixplus) -> {TOVIMIX_IP}:{TOVIMIX_PORT}")
    print(f"• Auto-Sync interval          : Active every {sync_interval_time}ms -> /vimix/current/sync to port {TOVIMIX_PORT}")
    print(f"• Info Polling Engine         : Active every {info_requests_time}ms (/vimixplus/info/<nomeinfo>/<nomemedia>)")
    print(f"• Info Replies Path           : /vimixplus/replyinfo/<nomeinfo> <prop_name> <val> -> Port {REPLY_PORT}")
    print(f"• Local Media Selector Trigger: /vimixplus/selector [prev|next|current]")
    print(f"• Selector Reply Path         : /vimixplus/replyselector <name> <indice> <thumb0> -> Port {REPLY_PORT}")
    print(f"• Thumbnail Request Trigger   : /vimixplus/thumb/<index|name> <0..4|all>")
    print(f"• Thumbnail Reply Path        : /vimixplus/replythumb/<index|name>/<0..4> -> Port {REPLY_PORT}")
    print(f"• Current Source Tracking     : Active (/vimix/current/<idx> 1 -> source_current)")
    print("Waiting for incoming messages... (Press Ctrl+C to stop)\n")
    print("-" * 105)

    # Start the Auto-Sync Thread
    sync_thread = threading.Thread(target=sync_loop, daemon=True)
    sync_thread.start()

    # Start the Info Polling Thread
    info_thread = threading.Thread(target=info_polling_loop, daemon=True)
    info_thread.start()

    active_ports = [LISTEN_PORT, FROMVIMIX_PORT]
    threads = []
    
    for port in active_ports:
        thread = threading.Thread(
            target=start_server_instance, 
            args=(LISTEN_IP, port), 
            daemon=True
        )
        thread.start()
        threads.append(thread)

    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        print("\n\nAll servers stopped successfully.")

if __name__ == "__main__":
    main()
