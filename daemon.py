"""FastAPI web daemon for the Kramer VP-727 Web Controller.

Implements REST API endpoints, WebSocket server for state updates, and serves
the SPA frontend.
"""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import uvicorn
import yaml
from config import AppConfig, load_config, InputConfig
from connection import ScalerConnection

# Set up logging format and levels
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("kramer.daemon")

# Load configuration settings
config_file = Path("config.yaml")
try:
    config: AppConfig = load_config(config_file)
except Exception as e:
    logger.critical("Failed to load configuration: %s", e)
    raise SystemExit(1)

# Instantiate the shared connection manager
scaler_conn = ScalerConnection(config)


class WebSocketManager:
    """Manages active WebSocket connections for real-time state broadcast."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and store it."""
        await websocket.accept()
        self.active_connections.append(websocket)
        # Push initial status update
        try:
            await websocket.send_json(self.get_status_payload())
        except Exception:
            self.disconnect(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a closed WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_state(self) -> None:
        """Broadcast the current scaler state to all connected clients."""
        payload = self.get_status_payload()
        for connection in list(self.active_connections):
            try:
                await connection.send_json(payload)
            except Exception:
                self.disconnect(connection)

    def get_status_payload(self) -> dict:
        """Format the current status as a serializable dictionary."""
        return {
            "status": scaler_conn.status,
            "hardware": {
                "ip": config.hardware.scaler_ip,
                "port": config.hardware.scaler_port,
                "firmware_generation": scaler_conn.firmware_generation,
            },
            "state": {
                "program_source": scaler_conn.program_source,
                "preview_source": scaler_conn.preview_source,
                "panel_locked": scaler_conn.panel_locked,
                "program_input_type": scaler_conn.program_input_type,
                "preview_input_type": scaler_conn.preview_input_type,
            },
            "config": {
                "matrix": {
                    "inputs": {
                        str(k): v.model_dump() for k, v in config.matrix.inputs.items()
                    },
                    "outputs": {str(k): v for k, v in config.matrix.outputs.items()},
                },
                "custom_resolutions": {
                    "active_profile": config.custom_resolutions.active_profile,
                    "profiles": {
                        k: v.model_dump()
                        for k, v in config.custom_resolutions.profiles.items()
                    },
                },
            },
        }


ws_manager = WebSocketManager()


async def on_scaler_state_changed() -> None:
    """Callback triggered by the connection manager when scaler state changes."""
    logger.debug("State update detected, broadcasting to WebSockets...")
    await ws_manager.broadcast_state()


# Register our callback
scaler_conn.subscribe(on_scaler_state_changed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle context manager starting/stopping background tasks."""
    import anyio

    logger.info("Starting background scaler connection worker...")
    async with anyio.create_task_group() as tg:
        tg.start_soon(scaler_conn.run_loop)
        yield
        # On shutdown, task group cancellation closes connection automatically


app = FastAPI(
    title="Kramer VP-727 Web Controller",
    description="REST & WebSocket API Bridge for the Kramer VP-727 presentation scaler.",
    version="1.0.0",
    lifespan=lifespan,
)


# API Models
class RouteRequest(BaseModel):
    """API model for routing a matrix input to a destination bus."""

    source_input: int = Field(
        ...,
        ge=1,
        le=8,
        description="Physical input channel source (1-8)",
        examples=[3],
    )
    destination_bus: Literal["program", "preview"] = Field(
        ...,
        description="Target destination bus",
        examples=["preview"],
    )


class CustomResolutionRequest(BaseModel):
    """API model for writing custom resolution parameters."""

    htotal: int = Field(..., description="Horizontal Total pixels")
    hactive: int = Field(..., description="Horizontal Active pixels")
    hsync: int = Field(..., description="Horizontal Sync duration")
    hstart: int = Field(..., description="Horizontal Start position")
    vtotal: int = Field(..., description="Vertical Total lines")
    vactive: int = Field(..., description="Vertical Active lines")
    vsync: int = Field(..., description="Vertical Sync duration")
    vstart: int = Field(..., description="Vertical Start position")


class OsdRequest(BaseModel):
    """API model for setting the OSD menu display state."""

    state: bool = Field(
        ...,
        description="OSD display state (true to display OSD, false to hide it)",
        examples=[True],
    )


class OutputResolutionRequest(BaseModel):
    """API model for setting the output resolution on Preview or Program bus."""

    bus: Literal["program", "preview"] = Field(
        ...,
        description="Target destination bus",
        examples=["preview"],
    )
    resolution_id: int = Field(
        ...,
        ge=0,
        le=17,
        description="Resolution ID mapping (0 to 17)",
        examples=[16],
    )


class InputTypeRequest(BaseModel):
    """API model for setting the input signal type of a bus (Program or Preview)."""

    bus: Literal["program", "preview"] = Field(
        ...,
        description="Target destination bus",
        examples=["preview"],
    )
    type_id: int = Field(
        ...,
        ge=0,
        le=8,
        description="Input signal type ID (0=RGBHV, 1=RGBS(PC), 2=RGsB(PC), 3=HD Component, 4=SD Component, 5=RGBS(Video), 6=RGsB(Video), 7=Y/C, 8=Video)",
        examples=[8],
    )


@app.get("/api/v1/status")
async def get_status() -> dict:
    """Fetch connection status, cached firmware generation, and state configurations."""
    return ws_manager.get_status_payload()


@app.post("/api/v1/route", status_code=202)
async def route_channel(payload: RouteRequest) -> dict:
    """Route input channel to specified destination bus (program or preview)."""
    ch_val = payload.source_input - 1
    cmd_id = 94 if payload.destination_bus == "program" else 42
    cmd = f"Y 0 {cmd_id} {ch_val}"

    try:
        await scaler_conn.send_command(cmd)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Failed to write route command: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"command_sent": cmd, "status": "acknowledged"}


@app.post("/api/v1/transition")
async def execute_transition() -> dict:
    """Trigger an immediate TAKE transition, swapping preview onto program."""
    cmd = "Y 0 16 3 1"

    try:
        await scaler_conn.send_command(cmd)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Failed to write transition command: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"action": "TAKE", "status": "success"}


@app.post("/api/v1/resolution/custom")
async def write_custom_resolution(payload: CustomResolutionRequest) -> dict:
    """Write custom geometric layout timing parameters down to non-volatile memory."""
    cmd = (
        f"Y 0 161 {payload.htotal} {payload.hactive} {payload.hsync} "
        f"{payload.hstart} {payload.vtotal} {payload.vactive} "
        f"{payload.vsync} {payload.vstart}"
    )

    try:
        await scaler_conn.send_command(cmd)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Failed to write resolution command: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {
        "profile_written": "User-Def",
        "parameters": {"hactive": payload.hactive, "vactive": payload.vactive},
        "status": "synchronized",
    }


@app.post("/api/v1/osd")
async def toggle_osd(payload: OsdRequest) -> dict:
    """Toggle the On-Screen Display (OSD) menu state on the physical unit."""
    val = 1 if payload.state else 0
    cmd = f"Y 0 200 {val}"

    try:
        await scaler_conn.send_command(cmd)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Failed to write OSD command: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"command_sent": cmd, "osd_state": "On" if payload.state else "Off"}


@app.post("/api/v1/resolution/output")
async def set_output_resolution(payload: OutputResolutionRequest) -> dict:
    """Set the active scaling output resolution for the Preview or Program bus."""
    cmd_id = 130 if payload.bus == "program" else 78
    cmd = f"Y 0 {cmd_id} {payload.resolution_id}"

    try:
        await scaler_conn.send_command(cmd)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Failed to set output resolution: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {
        "command_sent": cmd,
        "bus": payload.bus,
        "resolution_id": payload.resolution_id,
    }


@app.post("/api/v1/input/type")
async def set_input_type(payload: InputTypeRequest) -> dict:
    """Set the input signal type for the currently routed channel on Program or Preview bus."""
    cmd_id = 95 if payload.bus == "program" else 43
    cmd = f"Y 0 {cmd_id} {payload.type_id}"

    try:
        await scaler_conn.send_command(cmd)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Failed to write input type command: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {
        "command_sent": cmd,
        "bus": payload.bus,
        "type_id": payload.type_id,
        "status": "success",
    }


@app.post("/api/v1/config/inputs")
async def save_inputs_config(payload: dict[int, InputConfig]) -> dict:
    """Save the customized input labels and icons back to config.yaml."""
    config.matrix.inputs = payload

    try:
        config_data = config.model_dump()
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                config_data, f, default_flow_style=False, sort_keys=False
            )
        logger.info("New input labels successfully written to %s", config_file)
    except Exception as e:
        logger.exception("Failed to write to %s: %s", config_file, e)
        raise HTTPException(
            status_code=500, detail="Failed to write configuration file"
        )

    # Broadcast new state/config immediately to all WebSockets
    await ws_manager.broadcast_state()

    return {"status": "success", "message": "Configuration saved successfully"}


@app.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint to broadcast status updates in real-time."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep WebSocket connection alive and listen for any inbound text.
            # We don't process inbound WebSocket commands for now, only client keep-alive.
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected.")
    finally:
        ws_manager.disconnect(websocket)


# Serve SPA UI Frontend
@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    """Serve the single-page application dashboard frontend."""
    static_index = Path("static/index.html")
    if not static_index.exists():
        raise HTTPException(
            status_code=404,
            detail="Frontend static template file not found.",
        )
    return HTMLResponse(content=static_index.read_text(encoding="utf-8"))


def run_daemon() -> None:
    """Start the uvicorn web server based on config.yaml specifications."""
    uvicorn.run(
        "daemon:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.server.debug,
    )


if __name__ == "__main__":
    run_daemon()
