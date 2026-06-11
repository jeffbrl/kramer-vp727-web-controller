"""Configuration management for the Kramer VP-727 Web Controller.

This module defines the configuration schemas using Pydantic and handles
loading settings from a YAML file.
"""

from pathlib import Path
from typing import Dict, Any
import yaml
from pydantic import BaseModel


class ServerConfig(BaseModel):
    """Configuration settings for the API web server."""

    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False


class HardwareConfig(BaseModel):
    """Configuration settings for the physical Kramer VP-727 hardware switcher."""

    scaler_ip: str = "192.168.6.244"
    scaler_port: int = 5000
    connection_timeout_seconds: float = 2.0
    keepalive_interval_seconds: float = 30.0


class InputConfig(BaseModel):
    """Configuration for a single matrix input channel."""

    label: str
    icon: str = "help"


class MatrixConfig(BaseModel):
    """Configuration mapping the physical matrix inputs and outputs."""

    inputs: Dict[int, InputConfig]
    outputs: Dict[int, str]


class ResolutionProfile(BaseModel):
    """Details defining a custom display resolution and geometric timing parameters."""

    label: str
    htotal: int
    hactive: int
    hsync: int
    hstart: int
    vtotal: int
    vactive: int
    vsync: int
    vstart: int


class CustomResolutionsConfig(BaseModel):
    """Configuration grouping custom resolution profiles and active timing settings."""

    active_profile: str
    profiles: Dict[str, ResolutionProfile]


class AppConfig(BaseModel):
    """Root application configuration schema."""

    server: ServerConfig
    hardware: HardwareConfig
    matrix: MatrixConfig
    custom_resolutions: CustomResolutionsConfig


def load_config(config_path: Path | str = "config.yaml") -> AppConfig:
    """Load and parse the YAML configuration file.

    Args:
        config_path: Path to the yaml configuration file.

    Returns:
        An instantiated AppConfig containing the validated settings.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path.absolute()}")

    with open(path, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    return AppConfig.model_validate(data)
