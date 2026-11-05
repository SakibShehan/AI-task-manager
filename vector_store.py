import chromadb

from db import DATA_DIR

CHROMA_PATH = DATA_DIR / "chroma"
COLLECTION_NAME = "tasks"

_collection = None


def get_collection():
    """Create the client and collection once, then reuse them."""
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _document(task):
    """The text that gets embedded: title plus description."""
    text = task["title"]
    if task.get("description"):
        text += ". " + task["description"]
    return text


def upsert_task(task):
    """Add a task to the index, or overwrite it if the ID already exists."""
    get_collection().upsert(
        ids=[task["id"]],
        documents=[_document(task)],
        metadatas=[{"status": task["status"], "priority": task["priority"]}],
    )


def delete_task(task_id):
    """Remove a task from the index. Does nothing if it isn't there."""
    get_collection().delete(ids=[task_id])


def search(query, n=5):
    """Return up to n closest tasks as [{'id': ..., 'distance': ...}], best first."""
    collection = get_collection()
    total = collection.count()
    if total == 0 or not query.strip():
        return []

    result = collection.query(query_texts=[query], n_results=min(n, total))
    return [
        {"id": task_id, "distance": distance}
        for task_id, distance in zip(result["ids"][0], result["distances"][0])
    ]


def all_ids():
    """Every task ID currently in the index (used in commit 5 to rebuild it)."""
    return get_collection().get(include=[])["ids"]


def count():
    return get_collection().count()