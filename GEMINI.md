# Gemini Project Specification: Kramer VP-727 Web Controller

This specification outlines the architecture, data structures, API endpoints, and configuration schemas required to build a lightweight, production-grade web application to control a Kramer VP-727 presentation switcher over a local network interface using Python.

## 1. System Architecture

The application will act as a protocol bridge, exposing a modern HTTP REST API and WebSocket interface to the web frontend while translating operations into raw legacy Kramer Protocol 2000 TCP streams to communicate with the hardware on port 5000/50000.

+-------------------------------------------------------+
|                    Web Browser                        |
|   (Frontend Interface: Input Matrix / Take Controls)   |
+---------------------------+---------------------------+
|
HTTP / WebSockets (Real-time updates)
|
+---------------------------v---------------------------+
|                 Python Web Server                     |
|  - API Routing & Authentication Layer                 |
|  - Connection Pooler & Connection Keep-Alive          |
|  - Protocol 2000 Bitstream Encoder / Decoder          |
+---------------------------+---------------------------+
|
Raw TCP Socket Stream
|
+---------------------------v---------------------------+
|                 Kramer VP-727 Scaler                  |
|          (Listening on Port 5000 / 50000)             |
+-------------------------------------------------------+


## 2. Configuration Schema

The application configuration should be managed via a declarative YAML structure (`config.yaml`). This decouples the network topology and label manifests from the underlying control logic.

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  debug: false

hardware:
  scaler_ip: "192.168.6.244"
  scaler_port: 5000
  connection_timeout_seconds: 2.0
  keepalive_interval_seconds: 30.0

matrix:
  inputs:
    1:
      label: "Workstation VGA"
      icon: "desktop"
    2:
      label: "Retro PC (RGBHV)"
      icon: "terminal"
    3:
      label: "Test Bench (YPbPr)"
      icon: "flask"
    4:
      label: "Unassigned"
      icon: "help"
    5:
      label: "Unassigned"
      icon: "help"
    6:
      label: "Unassigned"
      icon: "help"
    7:
      label: "Unassigned"
      icon: "help"
    8:
      label: "Unassigned"
      icon: "help"
  outputs:
    1: "Program Bus"
    2: "Preview Bus"

custom_resolutions:
  active_profile: "WSXGA_60Hz"
  profiles:
    WSXGA_60Hz:
      label: "1680x1050 @ 60Hz"
      htotal: 2240
      hactive: 1680
      hsync: 176
      hstart: 296
      vtotal: 1089
      vactive: 1050
      vsync: 6
      vstart: 30

3. Kramer Protocol 2000 Command Structural MatrixThe back-end controller must translate incoming JSON payloads into structural space-delimited Protocol 2000 ASCII strings appended with a carriage return (\r).Operational IntentPython Token Data StructureOutbound Protocol 2000 StringExpected Hardware ResponseQuery Firmware["Y", "0", "57"]"Y 0 57\r""Z 0 57 4"Query Active Program["Y", "0", "91"]"Y 0 91\r""Z 0 91 <input> -1"Route to Program (Live Cut)["Y", "0", "1", "<in>", "1"]"Y 0 1 <in> 1\r""Z 0 1 <in> 1"Route to Preview (Staging)["Y", "0", "1", "<in>", "2"]"Y 0 1 <in> 2\r""Z 0 1 <in> 2"Execute TAKE Transition["Y", "0", "16", "3", "1"]"Y 0 16 3 1\r""Z 0 16 3 1"Write Custom Timing["Y", "0", "161", "HT", "HA", "HS", "HSt", "VT", "VA", "VS", "VSt"]"Y 0 161 2240 1680 176 296 1089 1050 6 30\r""Z 0 161 1"4. REST API Endpoint SpecificationGET /api/v1/statusFetches current connectivity state, cached firmware metrics, and structural hardware configurations.Success Response (200 OK):JSON{
  "status": "connected",
  "hardware": {
    "ip": "192.168.6.244",
    "port": 5000,
    "firmware_generation": 4
  },
  "state": {
    "program_source": 1,
    "preview_source": 5,
    "panel_locked": true
  }
}
POST /api/v1/routeChanges input configuration on a specified destination bus.Request Payload Type: application/jsonPayload Structure:JSON{
  "source_input": 3,
  "destination_bus": "preview"
}
Success Response (202 Accepted):JSON{
  "command_sent": "Y 0 1 3 2",
  "status": "acknowledged"
}
POST /api/v1/transitionTriggers an immediate electronic execution of the TAKE command, dissolving or wiping the preview bus onto the live program stream.Request Payload Type: EmptySuccess Response (200 OK):JSON{
  "action": "TAKE",
  "status": "success"
}
POST /api/v1/resolution/customForces a low-level write of a comprehensive geometric layout configuration down to the non-volatile user memory bank.Request Payload Type: application/jsonPayload Structure:JSON{
  "htotal": 2240,
  "hactive": 1680,
  "hsync": 176,
  "hstart": 296,
  "vtotal": 1089,
  "vactive": 1050,
  "vsync": 6,
  "vstart": 30
}
Success Response (200 OK):JSON{
  "profile_written": "User-Def",
  "parameters": { "hactive": 1680, "vactive": 1050 },
  "status": "synchronized"
}
5. Web UI RequirementsThe frontend interface should be functional, simple, and linear, avoiding heavy modern website layouts or high-overhead frameworks. It must provide:A Visual Status Header: Displays real-time heartbeat connectivity to the back-end daemon and the current active hardware firmware level.Matrix Control Panel: Two distinct rows of tactile interface blocks mapping the 8 hardware inputs.Row 1: Program Bus (Color-coded to highlight active live source).Row 2: Preview Bus (Color-coded to highlight active staged source).Dedicated Action Block: A prominent, heavy-accented action link representing the physical TAKE command to push transitions instantly.Custom Resolution Utility: A sub-panel parsing the explicit horizontal and vertical parameters configured in the schema, allowing users to verify or trigger manual EEPROM updates with a single interaction.6. Connection Management & Error StatesSocket Persistence: The application layer must maintain a persistent background TCP socket worker loop. Opening and tearing down a new socket for every discrete API payload is inefficient and causes latency spikes on legacy embedded network cards.Keep-Alive Threading: The network worker layer should send an automated non-destructive status query (Y 0 91\r) every 30 seconds to refresh the socket and prevent the scaler's internal card from dropping the connection idle.Retry Strategy: If a socket drops due to hardware thermal stress or a temporary network drop, the loop must implement exponential backoff retry logic up to a terminal threshold before flagging the frontend dashboard state as degraded.

