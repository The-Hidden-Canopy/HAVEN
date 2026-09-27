"""Candidate correlation: notice when two transports just described the same
physical device (native product-consolidation plan, P0 "Discovery" —
"a candidate correlation identity layer across providers").

This is deliberately narrow: `source_ip` is the only signal every current
IP-based transport (SSDP, mDNS) actually carries, so it is the only signal
used to group. A shared address is evidence, never identity by itself (the
same address can be reused by DHCP, or host several unrelated services) --
grouping never merges the underlying `DiscoveredDevice` records or hides
one behind another; it only tells a caller (a native Discover view, most
directly) which candidate ids likely name one device so it can render them
together instead of as unrelated rows. Bluetooth (no IP) and any future
transport with no shared signal simply forms its own singleton group.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import DiscoveredDevice


@dataclass(frozen=True)
class CorrelationGroup:
    group_id: str
    candidate_ids: tuple[str, ...]

    @property
    def is_correlated(self) -> bool:
        """True when this group actually merges more than one candidate --
        the common case (an IP seen by exactly one transport) is not
        "correlated," just a singleton group with nothing to merge."""

        return len(self.candidate_ids) > 1


def correlate(candidates: tuple[DiscoveredDevice, ...]) -> dict[str, CorrelationGroup]:
    """Map every candidate's id to the `CorrelationGroup` it belongs to.

    Grouping key: `source_ip` when present (so SSDP and mDNS candidates for
    the same LAN address land in one group); a candidate with no
    `source_ip` gets a private singleton group keyed by its own candidate
    id, since there is currently no cross-transport signal to correlate it
    by. Iteration order of `candidates` decides which ids list first within
    a group but never which candidate is treated as "the real one" -- this
    module makes no such judgment.
    """

    by_ip: dict[str, list[str]] = {}
    singleton_order: list[str] = []
    for candidate in candidates:
        if candidate.source_ip is not None:
            by_ip.setdefault(candidate.source_ip, []).append(candidate.candidate_id)
        else:
            singleton_order.append(candidate.candidate_id)

    groups: dict[str, CorrelationGroup] = {}
    for source_ip, candidate_ids in by_ip.items():
        group = CorrelationGroup(group_id=f"ip:{source_ip}", candidate_ids=tuple(candidate_ids))
        for candidate_id in candidate_ids:
            groups[candidate_id] = group
    for candidate_id in singleton_order:
        groups[candidate_id] = CorrelationGroup(group_id=f"solo:{candidate_id}", candidate_ids=(candidate_id,))
    return groups


__all__ = ["CorrelationGroup", "correlate"]
