"""S3: reingest_file must not lose a document when ingest fails.

Offline — uses an ephemeral chroma collection and a fake pipeline whose ingest
always raises, so it needs neither Ollama nor a persistent store."""

import chromadb
import pytest

import api.routers.chat as chat_router


class _FailingPipe:
    """Stand-in chat_pipe whose ingest always fails, to exercise the rollback."""

    def __init__(self, collection):
        self.collection = collection
        self._product_ids = []

    def ingest(self, path):
        raise RuntimeError("embedder unreachable")

    def _collect_product_ids(self):
        return []


@pytest.fixture
def failing_pipe():
    # EphemeralClient is a shared in-process singleton, so drop any collection
    # left over from a previous test before (re)creating a clean one.
    client = chromadb.EphemeralClient()
    name = "reingest_rollback_test"
    try:
        client.delete_collection(name)
    except Exception:  # noqa: BLE001 — absent is fine
        pass
    coll = client.create_collection(name)
    saved = chat_router.chat_pipe
    chat_router.chat_pipe = _FailingPipe(coll)
    yield coll
    chat_router.chat_pipe = saved
    try:
        client.delete_collection(name)
    except Exception:  # noqa: BLE001
        pass


def test_reingest_restores_old_chunks_when_ingest_fails(failing_pipe):
    coll = failing_pipe
    coll.add(
        ids=["doc.txt_0", "doc.txt_1"],
        documents=["RLENC1:old-a", "RLENC1:old-b"],  # stored (encrypted) form
        embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
        metadatas=[{"filename": "doc.txt"}, {"filename": "doc.txt"}],
    )

    # the re-ingest fails (embedder down) ...
    with pytest.raises(RuntimeError):
        chat_router.reingest_file("/kb/doc.txt")

    # ... but the original chunks are rolled back, not lost
    got = coll.get(where={"filename": "doc.txt"})
    assert sorted(got["ids"]) == ["doc.txt_0", "doc.txt_1"]
    assert sorted(got["documents"]) == ["RLENC1:old-a", "RLENC1:old-b"]


def test_reingest_failure_on_new_file_leaves_nothing(failing_pipe):
    coll = failing_pipe
    # a brand-new file has no prior chunks: a failed ingest rolls back to empty
    # and the restore path is a clean no-op (no crash on the empty snapshot)
    with pytest.raises(RuntimeError):
        chat_router.reingest_file("/kb/brand-new.txt")
    assert coll.get(where={"filename": "brand-new.txt"})["ids"] == []
