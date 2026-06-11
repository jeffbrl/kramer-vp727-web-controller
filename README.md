# Kramer VP-727 Web Controller

A lightweight, modern web bridge and user interface for controlling the **Kramer VP-727 In-Rator™** presentation switcher over a network connection.

This application translates RESTful HTTP API calls and WebSocket events into raw legacy **Kramer Protocol 2000** command streams, allowing real-time, interactive management of input routing, transitions, output resolutions, custom display timings, and OSD configuration via a glassmorphic dark-mode web application.

> [!WARNING]
> **Lack of Authentication**: This application does not implement any form of authentication, authorization, or access control. Anyone with network access to this service can execute route switches, perform transitions, modify custom timings, toggle the OSD, or rewrite the non-volatile EEPROM memory. Restrict network access to trusted users/devices, or place this service behind a reverse proxy (e.g., NGINX or Caddy) with enabled authentication headers.

---

## Architecture Overview

The system operates as an asynchronous protocol bridge running in Python using FastAPI. A background TCP socket worker loop keeps a persistent connection alive with the physical hardware, managing reconnection backoff and periodic keep-alive queries.

```
+-------------------------------------------------------+
|                    Web Browser                        |
|   (Dashboard: Routing, Take, Resolutions, Labels)     |
+---------------------------+---------------------------+
                            |
           HTTP / WebSockets (Real-time updates)
                            |
+---------------------------v---------------------------+
|                 Python Web Server                     |
|  - FastAPI Routing & Connection Management            |
|  - Persistent Socket Keep-Alive & Retry Loop          |
|  - Protocol 2000 Bitstream Encoder / Decoder          |
+---------------------------+---------------------------+
                            |
                  Raw TCP Socket Stream
                            |
+---------------------------v---------------------------+
|                 Kramer VP-727 Scaler                  |
|               (Listening on Port 5000)                |
+-------------------------------------------------------+
```

---

## Features

- **Modern Glassmorphic UI**: High-fidelity dark mode interface with interactive buttons, live routing statuses, responsive styling, and Lucide icons.
- **WebSocket Synchronization**: Live, bi-directional state updates broadcast automatically to all open browser clients whenever the physical switcher state changes.
- **Persistent Socket Threading**: The backend maintains a single TCP socket stream to minimize latency spikes on legacy embedded network cards, automatically running non-destructive keep-alive checks (`Y 0 91\r`) every 30 seconds.
- **Auto-Reconnection**: Dynamic retry logic with exponential backoff shields the system from temporary physical network drops or thermal switcher stress.
- **Input Customization Utility**: Assign friendly names and representative icons to all 8 physical inputs via the web UI. Configurations persist directly to `config.yaml` on the server and update the UI in real-time.
- **Standard & Custom Resolutions**:
  - Quickly switch standard scaling output resolutions (e.g., 1024x768, 720p, 1080p) for both Program and Preview buses.
  - Tweak, preview, and burn horizontal and vertical geometric timing layouts directly to the switcher's non-volatile "User Define" EEPROM slot.
- **OSD Controls**: Integrated toggle button to open or close the switcher's physical On-Screen Display menu.

---

## Getting Started

### Prerequisites
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (recommended Python package manager)

### Installation & Run

1. Clone this repository to your workspace:
   ```bash
   git clone https://github.com/jeffbrl/kramer-vp727-web-controller.git
   cd kramer-727
   ```

2. Synchronize project dependencies:
   ```bash
   uv sync
   ```

3. Launch the FastAPI server:
   ```bash
   uv run python main.py
   ```
   The application will boot on `http://0.0.0.0:8080`.

---

## Configuration Schema (`config.yaml`)

Settings are managed via a declarative YAML structure. Below is an example configuration file:

```yaml
server:
  host: 0.0.0.0
  port: 8080
  debug: false
hardware:
  scaler_ip: 192.168.6.244
  scaler_port: 5000
  connection_timeout_seconds: 2.0
  keepalive_interval_seconds: 30.0
matrix:
  inputs:
    1:
      label: SNES
      icon: desktop
    2:
      label: Genesis
      icon: desktop
    3:
      label: PC Engine
      icon: desktop
    4:
      label: Unassigned
      icon: help
    5:
      label: Unassigned
      icon: help
    6:
      label: Unassigned
      icon: help
    7:
      label: Unassigned
      icon: help
    8:
      label: Unassigned
      icon: help
  outputs:
    1: Program Bus
    2: Preview Bus
custom_resolutions:
  active_profile: WSXGA_60Hz
  profiles:
    WSXGA_60Hz:
      label: 1680x1050 @ 60Hz
      htotal: 2240
      hactive: 1680
      hsync: 176
      hstart: 296
      vtotal: 1089
      vactive: 1050
      vsync: 6
      vstart: 30
```

---

## REST API Specification

### 1. `GET /api/v1/status`
Fetches connection health, current hardware state metrics, active configurations, and label mapping.
* **Success Response (200 OK):**
  ```json
  {
    "status": "connected",
    "hardware": {
      "ip": "192.168.6.244",
      "port": 5000,
      "firmware_generation": 4
    },
    "state": {
      "program_source": 1,
      "preview_source": 2,
      "panel_locked": false
    },
    "config": { ... }
  }
  ```

### 2. `POST /api/v1/route`
Routes an input channel source to a destination bus.
* **Request Payload:**
  ```json
  {
    "source_input": 3,
    "destination_bus": "preview"
  }
  ```
* **Success Response (202 Accepted):**
  ```json
  {
    "command_sent": "Y 0 1 3 2",
    "status": "acknowledged"
  }
  ```

### 3. `POST /api/v1/transition`
Triggers an immediate electronic execution of the `TAKE` command, swapping the Preview bus onto the Live Program stream.
* **Success Response (200 OK):**
  ```json
  {
    "command_sent": "Y 0 16 3 1",
    "status": "acknowledged"
  }
  ```

### 4. `POST /api/v1/resolution/output`
Configures the output scaling resolution of a specific destination bus to one of the switcher's standard resolution indices.
* **Request Payload:**
  ```json
  {
    "bus": "program",
    "resolution_id": 16
  }
  ```
* **Success Response (200 OK):**
  ```json
  {
    "command_sent": "Y 0 130 16",
    "bus": "program",
    "resolution_id": 16
  }
  ```

### 5. `POST /api/v1/resolution/custom`
Forces a low-level write of a comprehensive custom timing layout configuration down to the non-volatile user memory bank.
* **Request Payload:**
  ```json
  {
    "htotal": 2240,
    "hactive": 1680,
    "hsync": 176,
    "hstart": 296,
    "vtotal": 1089,
    "vactive": 1050,
    "vsync": 6,
    "vstart": 30
  }
  ```
* **Success Response (200 OK):**
  ```json
  {
    "command_sent": "Y 0 161 2240 1680 176 296 1089 1050 6 30",
    "status": "synchronized"
  }
  ```

### 6. `POST /api/v1/osd`
Toggles the On-Screen Display (OSD) menu visibility.
* **Request Payload:**
  ```json
  {
    "state": true
  }
  ```
* **Success Response (200 OK):**
  ```json
  {
    "command_sent": "Y 0 200 1",
    "osd_state": "On"
  }
  ```

### 7. `POST /api/v1/input/type`
Sets the input signal type (e.g. RGBHV, Composite, S-Video) for the currently routed channel on the Program or Preview bus.
* **Request Payload:**
  ```json
  {
    "bus": "preview",
    "type_id": 8
  }
  ```
* **Success Response (200 OK):**
  ```json
  {
    "command_sent": "Y 0 43 8",
    "bus": "preview",
    "type_id": 8,
    "status": "success"
  }
  ```

### 8. `POST /api/v1/config/inputs`
Saves custom input label descriptors and icons to the backend `config.yaml` file.
* **Request Payload:**
  ```json
  {
    "1": { "label": "Retro PC", "icon": "terminal" },
    "2": { "label": "SNES", "icon": "desktop" }
  }
  ```
* **Success Response (200 OK):**
  ```json
  {
    "status": "success",
    "message": "Configuration saved successfully"
  }
  ```

### 9. `WEBSOCKET /api/v1/ws`
Accepts WebSocket connections. Broadcasts the status payload to all connected clients whenever the hardware status, routing, or active configuration changes.

---

## Developer Workflow

### Quality Control

The project strictly follows PEP-8 code styling and Pydantic validation rules. You can verify syntax, types, and standard tests using:

```bash
# Run Ruff lint check
uv run ruff check .

# Run Pyright type validation
uv run pyright .

# Run unit and integration tests
PYTHONPATH=. uv run pytest
```
