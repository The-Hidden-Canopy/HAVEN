"""WiFi/LAN discovery: SSDP (UPnP) today, more provider types later."""

from .ssdp import SsdpDiscoveryProvider, WIFI_SSDP_PROVIDER_ID, parse_ssdp_response

__all__ = ["SsdpDiscoveryProvider", "WIFI_SSDP_PROVIDER_ID", "parse_ssdp_response"]
