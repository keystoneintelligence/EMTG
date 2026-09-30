"""Lightweight native scientific results. No optional application imports."""
from .parser import EMTGResultParser, ParsedEMTGResult, EXTRACTION_VERSION, expended_delta_v_km_s
from .inventory import ARTIFACT_INVENTORY_VERSION, artifact_inventory

__all__ = ['EMTGResultParser', 'ParsedEMTGResult', 'EXTRACTION_VERSION', 'expended_delta_v_km_s', 'ARTIFACT_INVENTORY_VERSION', 'artifact_inventory']
