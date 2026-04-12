"""Document ingestion pipeline — loads PDFs, chunks, embeds, and indexes.

Usage:
    python ingest.py

Steps:
    1. Load PDFs from data/ directory (PyPDFLoader)
    2. Split into chunks (RecursiveCharacterTextSplitter, 500/100)
    3. Generate embeddings (text-embedding-3-small via OpenAI)
    4. Build FAISS vector store, save to index/
    5. Pickle raw chunks for BM25 retriever
"""

import pickle
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import Settings


def ingest():
    settings = Settings()
    data_dir = Path(settings.data_dir)
    index_dir = Path(settings.index_dir)

    docs = []
    pdf_files = sorted(data_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {data_dir}/")
        return

    for pdf_file in pdf_files:
        print(f"Loading {pdf_file.name}...")
        loader = PyPDFLoader(str(pdf_file))
        loaded = loader.load()
        docs.extend(loaded)
        print(f"  -> {len(loaded)} pages")

    print(f"\nTotal pages loaded: {len(docs)}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    chunks = splitter.split_documents(docs)
    print(
        f"Split into {len(chunks)} chunks "
        f"(size={settings.chunk_size}, overlap={settings.chunk_overlap})"
    )

    print(f"\nGenerating embeddings with {settings.embedding_model}...")
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key.get_secret_value(),
    )
    vectorstore = FAISS.from_documents(chunks, embeddings)

    index_dir.mkdir(parents=True, exist_ok=True)
    vectorstore.save_local(str(index_dir))
    print(f"FAISS index saved to {index_dir}/")

    chunks_path = index_dir / "bm25_chunks.pkl"
    with open(chunks_path, "wb") as f:
        pickle.dump(chunks, f)
    print(f"BM25 chunks saved to {chunks_path} ({len(chunks)} chunks)")

    print("\nIngestion complete!")


if __name__ == "__main__":
    ingest()
