```markdown
# VimixPlus OSC Router

**`vimixplus`** is an advanced, multi-port asynchronous OSC (Open Sound Control) router written in Python. It acts as an intelligent proxy layer between external controllers (e.g., Pure Data, TouchOSC, QLab, Max/MSP) and **Vimix**.

It extends Vimix with powerful new capabilities:
* **State Mirroring**: Caches all 26 standard Vimix properties in RAM.
* **On-Demand & Synchronous Thumbnail Extraction**: Automatically generates and buffers 5 JPEG frames in RAM per media using FFmpeg/FFprobe.
* **Selective Property Polling Engine**: Periodically polls media properties and streams responses back **only when property values actually change**.
* **Sequential Media Selector**: Provides cyclic navigation across loaded media items (`next`, `prev`, `current`) with automatic URI sync and instant thumbnail delivery.
* **Dual Resolution (Index/Name)**: Accepts both numeric Vimix indices (e.g., `/vimix/0/alpha`) and string media names (e.g., `/vimix/camera_1/alpha`).

---

## Network Architecture & Port Assignment


```

[ External Controller ]
│
│ (6666 - Input)
▼
┌────────────────────────────────────────────────────────┐
│                      VIMIXPLUS                         │ ── (7000 - Output) ──► [ Vimix ]
│                 (State Engine & RAM)                   │ ◄─ (6667 - Input) ───┤
└────────────────────────────────────────────────────────┘
│
│ (6668 - Output Replies)
▼
[ External Controller ]

```

| Port Variable | Port | Direction | Description |
| :--- | :---: | :---: | :--- |
| `LISTEN_PORT` | **6666** | Input | Receives `/vimixplus/` custom commands and forwards standard `/vimix/` commands to Vimix. |
| `FROMVIMIX_PORT` | **6667** | Input | Receives state feedback, sync status, and property updates broadcasted by Vimix. |
| `REPLY_PORT` | **6668** | Output | Emits async local replies (`replyinfo`, `replyselector`, JPEG blobs) back to the controller. |
| `TOVIMIX_PORT` | **7000** | Output | Forwards valid control and sync query messages directly to Vimix. |

---

## Prerequisites & Installation

### Requirements
* **Python 3.8+**
* **FFmpeg / FFprobe** installed and accessible in system `PATH`.
* **python-osc** library.

### Installation Steps

1. Install Python dependencies:
   ```bash
   pip install python-osc

```

2. Ensure FFmpeg is installed:
```bash
ffmpeg -version
ffprobe -version

```


3. Run the router:
```bash
python vimixplus.py

```



---

## Global Configuration Parameters

You can adjust these settings at the top of `vimixplus.py`:

```python
LOG_LEVEL = 1          # 0 = Restrictive, 1 = State Changes, 2 = Full OSC Traffic
sync_interval_time = 2000  # Interval in ms for /vimix/current/sync requests (default: 2s)
info_requests_time = 500   # Polling interval in ms for /vimixplus/info/ requests

```

---

## OSC Protocol Reference — Port 6666 (Input)

Messages sent to Port **6666** are handled locally if they begin with `/vimixplus/`. Standard `/vimix/` control messages are passed transparently to Port **7000**.

### 1. `/vimixplus/selector`

Navigates cyclically through active loaded media.

* **Path:** `/vimixplus/selector`
* **Argument:** `[action]` (*string, optional* — `"next"`, `"prev"`, or `"current"`. Default: `"current"`)
* **Behavior:**
1. Increments, decrements, or maintains the current `selector_source` index.
2. If the URI is missing from RAM, forces a sync request to Vimix and waits up to 500ms for response.
3. If thumbnails are missing, generates them **synchronously** before replying.


* **Reply Sent to Port 6668:**
* **Path:** `/vimixplus/replyselector`
* **Arguments:** `[media_name, selector_index, thumb0_blob]`
* `media_name` (*string*): Media name string.
* `selector_index` (*int*): Current `selector_source` index.
* `thumb0_blob` (*blob*): Binary JPEG bytes of the first frame (or `b"\x00"` fallback byte).





---

### 2. `/vimixplus/info/<nomeinfo>/<nomemedia>`

Registers a periodic background polling task for specific properties of a media item.

* **Path:** `/vimixplus/info/<nomeinfo>/<nomemedia>`
* **Arguments:** `[prop1, prop2, prop3, ...]` (*strings*, or `"all"`)
* **Example:** `/vimixplus/info/video1_status/07-Ritual-filo alpha seek speed`
* **Behavior:** Requests target properties from Vimix every `info_requests_time` ms.
* **Reply Sent to Port 6668:**
* Emitted **ONLY when a value changes**:
* **Path:** `/vimixplus/replyinfo/<nomeinfo>`
* **Arguments:** `[prop_name, new_value]`



---

### 3. `/vimixplus/thumb/<target>`

Retrieves pre-generated JPEG frame thumbnails from RAM.

* **Path:** `/vimixplus/thumb/<target>` (*`<target>` can be an integer index or a media string name*)
* **Arguments:** `[thumb_index]` (*string/int*: `0`, `1`, `2`, `3`, `4`, or `"all"`)
* **Example:** `/vimixplus/thumb/camera_1 0`
* **Reply Sent to Port 6668:**
* **Path:** `/vimixplus/replythumb/<target>/<thumb_index>`
* **Arguments:** `[JPEG_blob]` (*binary JPEG data*)



---

### 4. `/vimixplus/sync/<target>`

Triggers an immediate `/vimix/<target>/get` query to Vimix.

* **Path:** `/vimixplus/sync/<target>`
* **Arguments:** `[prop1, prop2, ...]` (*optional, defaults to `"all"*`)

---

### 5. `/vimixplus/showdata`

Prints clean ASCII diagnostic tables in the terminal.

* **Path:** `/vimixplus/showdata`
* **Arguments:** *None*
* **Output:** Displays current state tables for active Vimix sources, active polling tasks, and local selector position.

---

### 6. Transparent Pass-Through (`/vimix/...`)

* **Path:** Any non-`vimixplus` path (e.g., `/vimix/0/alpha 0.5`, `/vimix/camera_1/play 1`)
* **Behavior:** Forwarded directly to Vimix on Port **7000**.

---

## OSC Message Reference — Port 6667 (Vimix Feedback Input)

Port **6667** handles incoming updates broadcasted directly by Vimix.

| Address | Argument(s) | Internal Action |
| --- | --- | --- |
| `/vimix/current/<index>` | `1` / `1.0` | Updates `source_current` global variable. |
| `/vimix/<target>/<property>` | `<value>` | Updates local RAM state cache. Triggers FFmpeg thumbnail generation if `uri` changes. Sends `replyinfo` updates to Port **6668** if values changed. |

---

## Summary Protocol Table

| Trigger Message | In Port | Arguments | Response Message | Reply Port |
| --- | --- | --- | --- | --- |
| `/vimixplus/selector` | **6666** | `[next|prev|current]` | `/vimixplus/replyselector <name> <idx> <thumb0>` | **6668** |
| `/vimixplus/info/<info>/<media>` | **6666** | `[props...]` | `/vimixplus/replyinfo/<info> <prop> <val>` *(on change)* | **6668** |
| `/vimixplus/thumb/<target>` | **6666** | `[0..4|all]` | `/vimixplus/replythumb/<target>/<idx> <blob>` | **6668** |
| `/vimixplus/sync/<target>` | **6666** | `[props...]` | *(Forwards GET request to Vimix)* | — |
| `/vimixplus/showdata` | **6666** | *None* | *(Prints diagnostic tables to terminal)* | — |
| `/vimix/<target>/<prop>` | **6666** | `<value>` | *(Forwards directly to Vimix Port 7000)* | — |
| `/vimix/<target>/<prop>` | **6667** | `<value>` | Updates local RAM state & triggers registered replies | **6668** |

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See the `LICENSE` file for more details.

```

```
