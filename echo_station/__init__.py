"""
ECHO Station - BLE GATT server for ECHO personality devices
"""

__version__ = "0.1.0"

# Lazy import to avoid import errors when BLE dependencies not installed
def __getattr__(name):
    if name == "EchoStation":
        from .station import EchoStation
        return EchoStation
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = ["EchoStation"]
