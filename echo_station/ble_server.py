"""
BLE Server - BlueZ GATT server for ECHO station
Uses DBus to communicate with BlueZ
"""

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop, threads_init
import logging
from typing import Optional, Callable
import sys

from .config import (
    BLE_DEVICE,
    SERVICE_UUID,
    CHARACTERISTIC_UUID,
    STATION_NAMES,
    ADVERTISING_INTERVAL,
    ADVERTISING_SERVICE_UUIDS,
)

logger = logging.getLogger(__name__)

# BlueZ D-Bus paths and interfaces
BLUEZ_SERVICE_NAME = "org.bluez"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_APPLICATION_IFACE = "org.bluez.GattApplication1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHARACTERISTIC_IFACE = "org.bluez.GattCharacteristic1"
GATT_DESCRIPTOR_IFACE = "org.bluez.GattDescriptor1"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"


class EchoAdvertisement(dbus.service.Object):
    """
    org.bluez.LEAdvertisement1 — LocalName + ServiceUUIDs.

    Do not set ``Includes`` to ``uuid-128`` and ``local-name`` together with a
    full 128-bit UUID and a long ``LocalName``: BlueZ often returns
    ``Failed to parse advertisement`` (legacy AD is 31 bytes; over-constrained
    layouts are rejected). The BlueZ ``example-advertisement`` pattern uses only
    ``tx-power`` in ``Includes`` while still advertising ``LocalName`` and
    ``ServiceUUIDs``; the stack splits data across AD and scan response as fit.
    """

    def __init__(self, bus, path: str, local_name: str, service_uuids: list):
        dbus.service.Object.__init__(self, bus, path)
        self._path = path
        self._local_name = local_name
        if not service_uuids:
            raise ValueError("LE advertisement requires at least one ServiceUUID")
        self._service_uuids = list(service_uuids)

    def get_path(self) -> dbus.ObjectPath:
        return dbus.ObjectPath(self._path)

    def _le_props(self) -> dict:
        props = {
            "Type": dbus.String("peripheral"),
            "ServiceUUIDs": dbus.Array(
                [dbus.String(u) for u in self._service_uuids],
                signature="s",
            ),
            "LocalName": dbus.String(self._local_name),
        }
        # Match bluez/test/example-advertisement: optional TX only in Includes.
        props["Includes"] = dbus.Array(
            [dbus.String("tx-power")], signature="s"
        )
        return props

    @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface_name):
        if interface_name != LE_ADVERTISEMENT_IFACE:
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.InvalidArgs",
                "Invalid interface",
            )
        return self._le_props()

    @dbus.service.method(PROPERTIES_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface_name, prop_name):
        if interface_name != LE_ADVERTISEMENT_IFACE:
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.InvalidArgs",
                "Invalid interface",
            )
        props = self._le_props()
        if prop_name not in props:
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.InvalidArgs",
                "Unknown property",
            )
        return props[prop_name]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        logger.info("LE advertisement released by BlueZ")


class Characteristic(dbus.service.Object):
    """GATT Characteristic definition"""

    def __init__(self, bus, path, uuid, flags, value_callback):
        dbus.service.Object.__init__(self, bus, path)
        self.uuid = uuid
        self.flags = flags
        self.value = []
        self.value_callback = value_callback

    @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface_name):
        if interface_name != GATT_CHARACTERISTIC_IFACE:
            raise dbus.exceptions.DBusException("Invalid interface")

        return {
            "UUID": dbus.String(self.uuid),
            "Service": dbus.ObjectPath(self.get_service_path()),
            "Value": dbus.Array(self.value, signature="y"),
            "Notifying": dbus.Boolean(False),
            "Flags": dbus.Array(self.flags, signature="s"),
        }

    @dbus.service.method(PROPERTIES_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface_name, property_name):
        if interface_name != GATT_CHARACTERISTIC_IFACE:
            raise dbus.exceptions.DBusException("Invalid interface")

        if property_name == "UUID":
            return dbus.String(self.uuid)
        elif property_name == "Flags":
            return dbus.Array(self.flags, signature="s")
        elif property_name == "Value":
            return dbus.Array(self.value, signature="y")

        raise dbus.exceptions.DBusException("Invalid property")

    @dbus.service.method(GATT_CHARACTERISTIC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        logger.debug(f"ReadValue called: {self.value}")
        return dbus.Array(self.value, signature="y")

    @dbus.service.method(GATT_CHARACTERISTIC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        """Handle write operations from BLE client"""
        try:
            data = bytes(value)
            text = data.decode("utf-8").strip()
            # Strip BOM / leading NUL so ECHO_* prefix lines match upload_handler (otherwise
            # lines look like "ECHO_STATE_JSON:..." in logs but startswith fails → "malformed").
            text = text.lstrip("\ufeff\x00\u200b\u200c\u200d\u2060")
            logger.debug(f"WriteValue: {text}")

            if self.value_callback:
                self.value_callback(text)

            self.value = value
        except Exception as e:
            logger.error(f"Error in WriteValue: {e}")
            raise dbus.exceptions.DBusException(f"Write failed: {e}")

    def get_service_path(self):
        """Return the path of the parent service"""
        return "/org/echo/service"


class Service(dbus.service.Object):
    """GATT Service definition"""

    def __init__(self, bus, path, uuid, primary=True):
        dbus.service.Object.__init__(self, bus, path)
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []

    def add_characteristic(self, characteristic):
        self.characteristics.append(characteristic)

    @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface_name):
        if interface_name != GATT_SERVICE_IFACE:
            raise dbus.exceptions.DBusException("Invalid interface")

        return {
            "UUID": dbus.String(self.uuid),
            "Primary": dbus.Boolean(self.primary),
            "Characteristics": dbus.Array(
                [c.get_path() for c in self.characteristics],
                signature="o"
            ),
        }

    @dbus.service.method(PROPERTIES_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface_name, property_name):
        if interface_name != GATT_SERVICE_IFACE:
            raise dbus.exceptions.DBusException("Invalid interface")

        if property_name == "UUID":
            return dbus.String(self.uuid)
        elif property_name == "Primary":
            return dbus.Boolean(self.primary)

        raise dbus.exceptions.DBusException("Invalid property")

    def get_path(self):
        return "/org/echo/service"


class GattApplication(dbus.service.Object):
    """GATT Application - root object for GATT service"""

    def __init__(self, bus):
        dbus.service.Object.__init__(self, bus, "/org/echo")
        self.services = {}

    def add_service(self, service):
        self.services[service.get_path()] = service

    @dbus.service.method(OBJECT_MANAGER_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        response = {}
        response["/org/echo"] = {
            GATT_APPLICATION_IFACE: {}
        }

        # Add service
        for path, service in self.services.items():
            response[path] = {
                GATT_SERVICE_IFACE: {
                    "UUID": dbus.String(service.uuid),
                    "Primary": dbus.Boolean(service.primary),
                }
            }

            # Add characteristics
            for char in service.characteristics:
                char_path = char.path
                response[char_path] = {
                    GATT_CHARACTERISTIC_IFACE: {
                        "UUID": dbus.String(char.uuid),
                        "Service": dbus.ObjectPath(service.get_path()),
                        "Flags": dbus.Array(char.flags, signature="s"),
                        "Value": dbus.Array(char.value, signature="y"),
                        "Notifying": dbus.Boolean(False),
                    }
                }

        return response


class BLEServer:
    """BlueZ-based BLE GATT server"""

    def __init__(self, station_names=None, on_data_received: Optional[Callable] = None):
        self.station_names = station_names or STATION_NAMES
        self.on_data_received = on_data_received
        self.bus = None
        self.adapter = None
        self.app = None
        self.advertisement = None
        self.le_advertisement = None
        self.running = False

    def initialize(self) -> bool:
        """Initialize BLE server"""
        try:
            # Setup DBus
            DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus()

            # Get adapter
            adapter_path = f"/org/bluez/{BLE_DEVICE}"
            self.adapter = self.bus.get_object(BLUEZ_SERVICE_NAME, adapter_path)

            logger.info(f"Using Bluetooth adapter: {BLE_DEVICE}")

            # Create GATT application (registration deferred until GLib loop runs;
            # otherwise BlueZ calls GetManagedObjects and we deadlock → NoReply)
            self._setup_gatt_application()

            logger.info("BLE Server prepared (GATT registration deferred)")
            return True

        except dbus.exceptions.DBusException as e:
            logger.error(f"DBus error during initialization: {e}")
            return False
        except Exception as e:
            logger.error(f"Error initializing BLE server: {e}")
            return False

    def register_application_async(
        self, on_success: Callable[[], None], on_error: Callable[[Exception], None]
    ) -> None:
        """
        Register GATT with BlueZ without blocking the GLib loop.
        BlueZ calls GetManagedObjects on our app during registration; the loop
        must keep running to answer, so this uses dbus async handlers.
        """
        manager = dbus.Interface(self.adapter, GATT_MANAGER_IFACE)

        def reply_handler(*args, **kwargs):
            try:
                logger.info("GATT application registered")
                on_success()
            except Exception as e:
                logger.exception("After GATT registration: %s", e)
                on_error(e)

        def error_handler(e: dbus.exceptions.DBusException):
            logger.error("Failed to register GATT app: %s", e)
            on_error(e)

        manager.RegisterApplication(
            dbus.ObjectPath("/org/echo"),
            dbus.Dictionary(signature="sv"),
            reply_handler=reply_handler,
            error_handler=error_handler,
        )

    def _setup_gatt_application(self):
        """Setup GATT service and characteristics"""
        self.app = GattApplication(self.bus)

        # Create service
        service = Service(self.bus, "/org/echo/service", SERVICE_UUID, primary=True)

        # Create characteristic for data reception
        char_path = "/org/echo/characteristic"
        characteristic = Characteristic(
            self.bus,
            char_path,
            CHARACTERISTIC_UUID,
            ["write", "read"],
            self.on_data_received,
        )
        characteristic.path = char_path
        characteristic.get_path = lambda: char_path

        service.add_characteristic(characteristic)
        self.app.add_service(service)

        logger.debug(f"GATT app setup: service={SERVICE_UUID}, char={CHARACTERISTIC_UUID}")

    def start_advertising_async(self, on_done: Callable[[bool], None]) -> None:
        """
        Power adapter and register an LE advertisement with LocalName + service UUID.
        on_done(True) when BlueZ accepted the advertisement; must be async (DBus + GLib).
        """
        try:
            adapter_props = dbus.Interface(self.adapter, PROPERTIES_IFACE)
            adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
            adapter_props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(True))
            logger.info("Adapter powered and discoverable")

            local_name = (
                self.station_names[0] if self.station_names else "ECHO_station_001"
            )
            uuids = list(ADVERTISING_SERVICE_UUIDS)
            if not uuids:
                logger.error(
                    "ADVERTISING_SERVICE_UUIDS is empty; cannot advertise service UUID"
                )
                on_done(False)
                return

            ad_path = "/org/echo/advertisement0"
            self._remove_le_advertisement_object()
            self.le_advertisement = EchoAdvertisement(
                self.bus,
                ad_path,
                local_name,
                uuids,
            )

            ad_manager = dbus.Interface(
                self.adapter, LE_ADVERTISING_MANAGER_IFACE
            )

            def reply_handler(*args, **kwargs):
                self.running = True
                logger.info(
                    "LE advertisement active: LocalName=%r ServiceUUIDs=%s",
                    local_name,
                    uuids,
                )
                on_done(True)

            def error_handler(e):
                logger.error("Failed to register LE advertisement: %s", e)
                self._remove_le_advertisement_object()
                on_done(False)

            ad_manager.RegisterAdvertisement(
                self.le_advertisement.get_path(),
                dbus.Dictionary({}, signature="sv"),
                reply_handler=reply_handler,
                error_handler=error_handler,
            )
        except Exception as e:
            logger.exception("start_advertising_async: %s", e)
            self._remove_le_advertisement_object()
            on_done(False)

    def _remove_le_advertisement_object(self) -> None:
        if self.le_advertisement is None:
            return
        try:
            self.le_advertisement.remove_from_connection()
        except Exception:
            pass
        self.le_advertisement = None

    def unregister_le_advertisement(self) -> None:
        """Unregister LE ad from BlueZ (best-effort, for shutdown)."""
        if self.adapter is None or self.le_advertisement is None:
            self._remove_le_advertisement_object()
            return
        try:
            ad_manager = dbus.Interface(
                self.adapter, LE_ADVERTISING_MANAGER_IFACE
            )
            ad_manager.UnregisterAdvertisement(
                self.le_advertisement.get_path()
            )
        except dbus.exceptions.DBusException as e:
            logger.warning("UnregisterAdvertisement: %s", e)
        self._remove_le_advertisement_object()

    def stop(self):
        """Stop the BLE server"""
        try:
            self.unregister_le_advertisement()
            if self.adapter:
                adapter_props = dbus.Interface(self.adapter, PROPERTIES_IFACE)
                adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(False))
                logger.info("BLE adapter powered off")

            self.running = False
        except Exception as e:
            logger.error(f"Error stopping BLE server: {e}")

    def is_running(self) -> bool:
        """Check if server is running"""
        return self.running
