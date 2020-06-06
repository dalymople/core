"""Config flow to configure Denon AVR receivers using their HTTP interface."""
from functools import partial
import logging
from urllib.parse import urlparse

import denonavr
from getmac import get_mac_address
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import ssdp
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_TIMEOUT
from homeassistant.helpers.device_registry import format_mac

from .receiver import ConnectDenonAVR

_LOGGER = logging.getLogger(__name__)

DOMAIN = "denonavr"

SUPPORTED_MANUFACTURERS = ["Denon", "DENON", "Marantz"]

CONF_SHOW_ALL_SOURCES = "show_all_sources"
CONF_ZONE2 = "zone2"
CONF_ZONE3 = "zone3"
CONF_TYPE = "type"
CONF_MODEL = "model"
CONF_MANUFACTURER = "manufacturer"

DEFAULT_SHOW_SOURCES = False
DEFAULT_TIMEOUT = 2
DEFAULT_ZONE2 = False
DEFAULT_ZONE3 = False

CONFIG_SCHEMA = vol.Schema({vol.Optional(CONF_HOST): str})

SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(
            int, vol.Range(min=1)
        ),
        vol.Optional(CONF_SHOW_ALL_SOURCES, default=DEFAULT_SHOW_SOURCES): bool,
        vol.Optional(CONF_ZONE2, default=DEFAULT_ZONE2): bool,
        vol.Optional(CONF_ZONE3, default=DEFAULT_ZONE3): bool,
    }
)


class DenonAvrFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Denon AVR config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize the Denon AVR flow."""
        self.host = None
        self.serial_number = None
        self.model_name = None
        self.timeout = DEFAULT_TIMEOUT
        self.show_all_sources = DEFAULT_SHOW_SOURCES
        self.zone2 = DEFAULT_ZONE2
        self.zone3 = DEFAULT_ZONE3
        self.d_receivers = []

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        errors = {}
        if user_input is not None:
            # check if IP address is set manually
            host = user_input.get(CONF_HOST)
            if host:
                self.host = host
                return await self.async_step_settings()

            # discovery using denonavr library
            self.d_receivers = await self.hass.async_add_executor_job(denonavr.discover)
            # More than one receiver could be discovered by that method
            if len(self.d_receivers) == 1:
                self.host = self.d_receivers[0]["host"]
                return await self.async_step_settings()
            if len(self.d_receivers) > 1:
                # show selection form
                return await self.async_step_select()

            errors["base"] = "discovery_error"

        return self.async_show_form(
            step_id="user", data_schema=CONFIG_SCHEMA, errors=errors
        )

    async def async_step_select(self, user_input=None):
        """Handle multiple receivers found."""
        errors = {}
        if user_input is not None:
            self.host = user_input["select_host"]
            return await self.async_step_settings()

        select_scheme = vol.Schema(
            {
                vol.Required("select_host"): vol.In(
                    [d_receiver["host"] for d_receiver in self.d_receivers]
                )
            }
        )

        return self.async_show_form(
            step_id="select", data_schema=select_scheme, errors=errors
        )

    async def async_step_settings(self, user_input=None):
        """Allow the user to specify settings."""
        if user_input is not None:
            # Get config option that have defaults
            self.timeout = user_input[CONF_TIMEOUT]
            self.show_all_sources = user_input[CONF_SHOW_ALL_SOURCES]
            self.zone2 = user_input[CONF_ZONE2]
            self.zone3 = user_input[CONF_ZONE3]

            return await self.async_step_connect()

        return self.async_show_form(step_id="settings", data_schema=SETTINGS_SCHEMA)

    async def async_step_connect(self, user_input=None):
        """Connect to the receiver."""
        connect_denonavr = ConnectDenonAVR(
            self.hass,
            self.host,
            self.timeout,
            self.show_all_sources,
            self.zone2,
            self.zone3,
        )
        if not await connect_denonavr.async_connect_receiver():
            return self.async_abort(reason="connection_error")
        receiver = connect_denonavr.receiver

        mac_address = await self.async_get_mac(self.host)

        if not self.serial_number:
            self.serial_number = receiver.serial_number
        if not self.model_name:
            self.model_name = (receiver.model_name).replace("*", "")

        if not self.serial_number:
            _LOGGER.error(
                "Could not get serial number of host %s, "
                "using the mac_address as identification",
                self.host,
            )
            if not mac_address:
                return self.async_abort(reason="no_mac")
            self.serial_number = mac_address

        unique_id = self.construct_unique_id(self.model_name, self.serial_number)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=receiver.name,
            data={
                CONF_HOST: self.host,
                CONF_MAC: mac_address,
                CONF_TIMEOUT: self.timeout,
                CONF_SHOW_ALL_SOURCES: self.show_all_sources,
                CONF_ZONE2: self.zone2,
                CONF_ZONE3: self.zone3,
                CONF_TYPE: receiver.receiver_type,
                CONF_MODEL: self.model_name,
                CONF_MANUFACTURER: receiver.manufacturer,
            },
        )

    async def async_step_ssdp(self, discovery_info):
        """Handle a discovered Denon AVR.

        This flow is triggered by the SSDP component. It will check if the
        host is already configured and delegate to the import step if not.
        """
        # Filter out non-Denon AVRs#1
        if (
            discovery_info.get(ssdp.ATTR_UPNP_MANUFACTURER)
            not in SUPPORTED_MANUFACTURERS
        ):
            return self.async_abort(reason="not_denonavr_manufacturer")

        # Check if required information is present to set the unique_id
        if (
            ssdp.ATTR_UPNP_MODEL_NAME not in discovery_info
            or ssdp.ATTR_UPNP_SERIAL not in discovery_info
        ):
            return self.async_abort(reason="not_denonavr_missing")

        self.model_name = discovery_info[ssdp.ATTR_UPNP_MODEL_NAME].replace("*", "")
        self.serial_number = discovery_info[ssdp.ATTR_UPNP_SERIAL]
        self.host = urlparse(discovery_info[ssdp.ATTR_SSDP_LOCATION]).hostname

        unique_id = self.construct_unique_id(self.model_name, self.serial_number)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured({CONF_HOST: self.host})

        return await self.async_step_settings()

    @staticmethod
    def construct_unique_id(model_name, serial_number):
        """Construct the unique id from the ssdp discovery or user_step."""
        return f"{model_name}-{serial_number}"

    async def async_get_mac(self, host):
        """Get the mac address of the DenonAVR receiver."""
        try:
            mac_address = await self.hass.async_add_executor_job(
                partial(get_mac_address, **{"ip": host})
            )
            if not mac_address:
                mac_address = await self.hass.async_add_executor_job(
                    partial(get_mac_address, **{"hostname": host})
                )
        except Exception as err:
            _LOGGER.error("Unable to get mac address: %s", err)
            mac_address = None

        if mac_address is not None:
            mac_address = format_mac(mac_address)
        return mac_address
