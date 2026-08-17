"""V2 文档处理适配器。"""

from infra.document_processing.ocr import RapidOcrDocumentAdapter
from infra.document_processing.parser import RiskPilotDocumentParser

__all__ = ["RapidOcrDocumentAdapter", "RiskPilotDocumentParser"]
