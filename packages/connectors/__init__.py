"""Connector interfaces for source-traced market, filing, macro, and industry data."""

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector
from packages.connectors.ecos import EcosConnector
from packages.connectors.edinet import EdinetConnector
from packages.connectors.estat import EStatConnector
from packages.connectors.finance_data_reader import FinanceDataReaderConnector
from packages.connectors.fred import FredConnector
from packages.connectors.jquants import JQuantsConnector
from packages.connectors.kosis import KosisConnector
from packages.connectors.marcap import MarcapConnector
from packages.connectors.opendart import OpenDartConnector
from packages.connectors.pykrx import PyKrxConnector
from packages.connectors.research_metadata import (
    HankyungConsensusMetadataConnector,
    NaverResearchSearchConnector,
)
from packages.connectors.sec import SecEdgarConnector
from packages.connectors.stooq import StooqConnector

__all__ = [
    "ConnectorDocument",
    "ConnectorRequest",
    "MarketConnector",
    "SecEdgarConnector",
    "OpenDartConnector",
    "EdinetConnector",
    "JQuantsConnector",
    "FredConnector",
    "EcosConnector",
    "KosisConnector",
    "EStatConnector",
    "FinanceDataReaderConnector",
    "MarcapConnector",
    "StooqConnector",
    "PyKrxConnector",
    "NaverResearchSearchConnector",
    "HankyungConsensusMetadataConnector",
]
