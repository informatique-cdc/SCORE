"""
Vector store models — metadata tracked in Django, vectors stored in sqlite-vec (vec0).

The vec0 virtual table is managed outside Django migrations (see vectorstore/store.py).
This module only holds Django-side metadata if needed beyond what's in DocumentChunk.
"""
