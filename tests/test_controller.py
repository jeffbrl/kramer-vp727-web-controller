"""Unit and integration tests for the Kramer VP-727 Web Controller.

Tests configuration loading, route parameter validation, transition executions,
custom resolution configurations, and API payload checks.
"""

from pathlib import Path
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
import pytest
from config import load_config
from daemon import app, scaler_conn

client = TestClient(app)


def test_load_config() -> None:
    """Validate loading settings from config.yaml schema."""
    config = load_config("config.yaml")
    assert config.server.port == 8080
    assert config.hardware.scaler_ip == "192.168.6.244"
    assert 1 in config.matrix.inputs
    assert isinstance(config.matrix.inputs[1].label, str)
    assert config.custom_resolutions.active_profile == "WSXGA_60Hz"


@pytest.mark.anyio
async def test_api_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate the GET /api/v1/status response mapping."""
    monkeypatch.setattr(scaler_conn, "status", "connected")
    monkeypatch.setattr(scaler_conn, "firmware_generation", 4)
    monkeypatch.setattr(scaler_conn, "program_source", 2)
    monkeypatch.setattr(scaler_conn, "preview_source", 3)

    response = client.get("/api/v1/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "connected"
    assert data["hardware"]["firmware_generation"] == 4
    assert data["state"]["program_source"] == 2
    assert data["state"]["preview_source"] == 3


@pytest.mark.anyio
async def test_api_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate routing POST request formats and mock string serialization."""
    mock_send = AsyncMock()
    monkeypatch.setattr(scaler_conn, "send_command", mock_send)

    # Route channel 3 to preview bus (destination bus 2)
    response = client.post(
        "/api/v1/route",
        json={"source_input": 3, "destination_bus": "preview"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "acknowledged"
    mock_send.assert_awaited_once_with("Y 0 42 2")

    # Route channel 1 to program bus (destination bus 1)
    mock_send.reset_mock()
    response = client.post(
        "/api/v1/route",
        json={"source_input": 1, "destination_bus": "program"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "acknowledged"
    mock_send.assert_awaited_once_with("Y 0 94 0")


@pytest.mark.anyio
async def test_api_route_invalid() -> None:
    """Verify routing requests reject out-of-bound or invalid arguments."""
    # Invalid channel ID (>8)
    response = client.post(
        "/api/v1/route",
        json={"source_input": 9, "destination_bus": "preview"},
    )
    assert response.status_code == 422

    # Invalid channel ID (<1)
    response = client.post(
        "/api/v1/route",
        json={"source_input": 0, "destination_bus": "preview"},
    )
    assert response.status_code == 422

    # Invalid bus name
    response = client.post(
        "/api/v1/route",
        json={"source_input": 3, "destination_bus": "main-mix"},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_api_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify transition POST endpoint serializes the proper TAKE command."""
    mock_send = AsyncMock()
    monkeypatch.setattr(scaler_conn, "send_command", mock_send)

    response = client.post("/api/v1/transition")
    assert response.status_code == 200
    assert response.json()["action"] == "TAKE"
    mock_send.assert_awaited_once_with("Y 0 16 3 1")


@pytest.mark.anyio
async def test_api_custom_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify custom resolution parameters serialize down to hardware specifications."""
    mock_send = AsyncMock()
    monkeypatch.setattr(scaler_conn, "send_command", mock_send)

    payload = {
        "htotal": 2240,
        "hactive": 1680,
        "hsync": 176,
        "hstart": 296,
        "vtotal": 1089,
        "vactive": 1050,
        "vsync": 6,
        "vstart": 30,
    }
    response = client.post("/api/v1/resolution/custom", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "synchronized"
    mock_send.assert_awaited_once_with("Y 0 161 2240 1680 176 296 1089 1050 6 30")


@pytest.mark.anyio
async def test_api_osd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify OSD menu toggle serialization works as expected."""
    mock_send = AsyncMock()
    monkeypatch.setattr(scaler_conn, "send_command", mock_send)

    # Toggle On
    response = client.post("/api/v1/osd", json={"state": True})
    assert response.status_code == 200
    assert response.json()["osd_state"] == "On"
    mock_send.assert_awaited_once_with("Y 0 200 1")

    # Toggle Off
    mock_send.reset_mock()
    response = client.post("/api/v1/osd", json={"state": False})
    assert response.status_code == 200
    assert response.json()["osd_state"] == "Off"
    mock_send.assert_awaited_once_with("Y 0 200 0")


@pytest.mark.anyio
async def test_api_output_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify output resolution selection routes the correct command numbers."""
    mock_send = AsyncMock()
    monkeypatch.setattr(scaler_conn, "send_command", mock_send)

    # Set Preview output resolution to 1080p (ID 16) -> Code 78
    response = client.post(
        "/api/v1/resolution/output",
        json={"bus": "preview", "resolution_id": 16},
    )
    assert response.status_code == 200
    assert response.json()["resolution_id"] == 16
    mock_send.assert_awaited_once_with("Y 0 78 16")

    # Set Program output resolution to 720p (ID 5) -> Code 130
    mock_send.reset_mock()
    response = client.post(
        "/api/v1/resolution/output",
        json={"bus": "program", "resolution_id": 5},
    )
    assert response.status_code == 200
    assert response.json()["resolution_id"] == 5
    mock_send.assert_awaited_once_with("Y 0 130 5")


@pytest.mark.anyio
async def test_api_save_inputs_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify writing configuration payload updates config.yaml and triggers broadcast."""
    import yaml
    import daemon

    # Mock writing config back to disk in tests by targeting a temp config file
    temp_config = tmp_path / "config.yaml"

    # Write initial dummy config
    dummy_data = {
        "server": {"host": "0.0.0.0", "port": 8080, "debug": False},
        "hardware": {
            "scaler_ip": "127.0.0.1",
            "scaler_port": 5000,
            "connection_timeout_seconds": 1.0,
            "keepalive_interval_seconds": 10.0,
        },
        "matrix": {
            "inputs": {
                i: {"label": f"Input {i}", "icon": "help"} for i in range(1, 9)
            },
            "outputs": {1: "Program", 2: "Preview"},
        },
        "custom_resolutions": {
            "active_profile": "test",
            "profiles": {
                "test": {
                    "label": "test",
                    "htotal": 100,
                    "hactive": 100,
                    "hsync": 10,
                    "hstart": 10,
                    "vtotal": 100,
                    "vactive": 100,
                    "vsync": 10,
                    "vstart": 10,
                }
            },
        },
    }
    with open(temp_config, "w", encoding="utf-8") as f:
        yaml.safe_dump(dummy_data, f)

    # Patch daemon's config_file path
    monkeypatch.setattr(daemon, "config_file", temp_config)

    # Send update request
    payload = {
        "hardware": {
            "scaler_ip": "127.0.0.1",
            "scaler_port": 5000,
        },
        "inputs": {
            i: {"label": f"Cam {i}", "icon": "desktop"} for i in range(1, 9)
        }
    }
    response = client.post("/api/v1/config", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify memory config and file config were updated
    assert daemon.config.matrix.inputs[1].label == "Cam 1"
    assert daemon.config.matrix.inputs[1].icon == "desktop"

    # Read back from temp file
    with open(temp_config, "r", encoding="utf-8") as f:
        saved_data = yaml.safe_load(f)
    assert saved_data["matrix"]["inputs"][1]["label"] == "Cam 1"
    assert saved_data["matrix"]["inputs"][1]["icon"] == "desktop"


@pytest.mark.anyio
async def test_set_input_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate setting the input signal type via POST /api/v1/input/type."""
    mock_send = AsyncMock()
    monkeypatch.setattr(scaler_conn, "send_command", mock_send)

    payload = {
        "bus": "preview",
        "type_id": 8,
    }
    response = client.post("/api/v1/input/type", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["bus"] == "preview"
    assert response.json()["type_id"] == 8
    mock_send.assert_called_once_with("Y 0 43 8")
