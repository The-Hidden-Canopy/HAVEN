"""WiFi/LAN discovery: SSDP (UPnP) and mDNS/DNS-SD."""

from .mdns import MdnsDiscoveryProvider, WIFI_MDNS_PROVIDER_ID, parse_dns_response
from .ssdp import SsdpDiscoveryProvider, WIFI_SSDP_PROVIDER_ID, parse_ssdp_response

__all__ = [
    "MdnsDiscoveryProvider",
    "SsdpDiscoveryProvider",
    "WIFI_MDNS_PROVIDER_ID",
    "WIFI_SSDP_PROVIDER_ID",
    "parse_dns_response",
    "parse_ssdp_response",
]
