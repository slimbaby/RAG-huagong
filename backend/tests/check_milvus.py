"""Debug script to check Milvus data."""
from pymilvus import connections, Collection

connections.connect(host="milvus", port="19530")
col = Collection("document_chunks")
col.load()
print(f"num_entities: {col.num_entities}")

if col.num_entities > 0:
    results = col.query(
        expr='chunk_id != ""',
        output_fields=["chunk_id", "content", "source_file"],
        limit=3,
    )
    for r in results:
        print(r)
else:
    print("No data in Milvus!")
