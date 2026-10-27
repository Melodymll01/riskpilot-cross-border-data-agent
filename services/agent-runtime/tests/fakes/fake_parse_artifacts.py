from domain.document_content import DocumentParseSnapshot


class FakeParseArtifactStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str, str], DocumentParseSnapshot] = {}

    def save(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
        snapshot: DocumentParseSnapshot,
    ) -> None:
        self.items[(owner_id, document_id, document_version_id, source_sha256)] = snapshot

    def get(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
    ) -> DocumentParseSnapshot | None:
        return self.items.get((owner_id, document_id, document_version_id, source_sha256))

    def delete(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
    ) -> bool:
        return (
            self.items.pop((owner_id, document_id, document_version_id, source_sha256), None)
            is not None
        )
