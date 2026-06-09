"""Vector Store module for Easy Agent

Supports ChromaDB with Sentence Transformers or ZhipuAI embeddings
"""

import logging
import os
import uuid
from typing import Optional

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except ImportError:
    chromadb = None
    ChromaSettings = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

try:
    from zhipuai import ZhipuAI
except ImportError:
    ZhipuAI = None

logger = logging.getLogger(__name__)


class VectorStore:
    """Vector store manager supporting ChromaDB"""

    def __init__(self, config: dict):
        self.enabled = config.get("enabled", False)
        self.db_path = config.get("db_path", "./data/chroma_db")
        self.collection_name = config.get("collection_name", "easy_agent_docs")
        self.embedding_provider = config.get(
            "embedding_provider", "sentence_transformers"
        )
        self.embedding_dimension = config.get("embedding_dimension", 1024)
        self.batch_size = config.get("batch_size", 32)

        if not self.enabled:
            logger.info("向量数据库: 已禁用")
            return

        os.makedirs(self.db_path, exist_ok=True)

        try:
            if chromadb is None:
                raise ImportError
        except ImportError:
            logger.error(
                "ChromaDB 未安装，请运行: pip install chromadb\n"
                "或者设置 vector_store.enabled=false 禁用向量数据库"
            )
            self._client = None
            return

        self._client = chromadb.PersistentClient(
            path=self.db_path, settings=ChromaSettings(anonymized_telemetry=False)
        )

        if self.embedding_provider == "sentence_transformers":
            self._embedding_fn = self._create_sentence_transformers_embedding()
        elif self.embedding_provider == "zhipu":
            self._embedding_fn = self._create_zhipu_embedding(config)
        else:
            logger.error(f"不支持的嵌入模型提供者: {self.embedding_provider}")
            self._client = None
            return

        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

        count = self._collection.count()
        logger.info(
            f"✅ 向量数据库初始化完成 | "
            f"类型: ChromaDB | "
            f"路径: {self.db_path} | "
            f"集合: {self.collection_name} | "
            f"文档数: {count} | "
            f"嵌入: {self.embedding_provider}"
        )

    def _create_sentence_transformers_embedding(self):
        model_name = (
            getattr(self, "config", {}).get(
                "sentence_transformers_model", "Qwen/Qwen3-Embedding-0.6B"
            )
            if hasattr(self, "config")
            else "Qwen/Qwen3-Embedding-0.6B"
        )
        try:
            if SentenceTransformer is None:
                raise ImportError

            logger.info(f"加载本地嵌入模型: {model_name} (首次使用需要下载)")
            model = SentenceTransformer(model_name, trust_remote_code=True)

            class STEmbeddingFunction:
                def __init__(self, model):
                    self.model = model

                def __call__(self, input):
                    texts = [t if t else "" for t in input]
                    embeddings = self.model.encode(texts, normalize_embeddings=True)
                    return embeddings.tolist()

            return STEmbeddingFunction(model)
        except ImportError:
            logger.error(
                "Sentence Transformers 未安装，请运行: pip install sentence-transformers"
            )
            raise

    def _create_zhipu_embedding(self, config: dict):
        api_key = config.get("zhipu_api_key", "")
        model_name = config.get("zhipu_model", "embedding-3")

        if not api_key:
            raise ValueError(
                "Zhipu AI API key is required when using zhipu embedding provider"
            )

        class ZhiPuEmbeddingFunction:
            def __init__(self, api_key: str, model: str):
                self.api_key = api_key
                self.model = model

            def __call__(self, input):
                client = ZhipuAI(api_key=self.api_key)
                texts = [t if t else "" for t in input]
                response = client.embeddings.create(model=self.model, input=texts)
                return [item.embedding for item in response.data]

        return ZhiPuEmbeddingFunction(api_key, model_name)

    @property
    def is_ready(self) -> bool:
        return self.enabled and self._client is not None

    def add_documents(
        self,
        documents: list[str],
        metadatas: Optional[list[dict]] = None,
        ids: Optional[list[str]] = None,
    ) -> int:
        if not self.is_ready:
            logger.warning("向量数据库未就绪，跳过添加文档")
            return 0

        if not ids:
            ids = [str(uuid.uuid4()) for _ in documents]

        total = len(documents)
        added = 0
        for i in range(0, total, self.batch_size):
            batch_end = min(i + self.batch_size, total)
            self._collection.add(
                documents=documents[i:batch_end],
                metadatas=metadatas[i:batch_end] if metadatas else None,
                ids=ids[i:batch_end],
            )
            added += batch_end - i

        logger.info(f"向量数据库: 添加 {added} 个文档")
        return added

    def search(
        self,
        query: str,
        n_results: int = 5,
        where: Optional[dict] = None,
    ) -> list[dict]:
        if not self.is_ready:
            logger.warning("向量数据库未就绪，返回空结果")
            return []

        results = self._collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
        )

        formatted = []
        if results.get("ids") and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                formatted.append(
                    {
                        "id": doc_id,
                        "document": results["documents"][0][i]
                        if results.get("documents")
                        else "",
                        "metadata": results["metadatas"][0][i]
                        if results.get("metadatas")
                        else {},
                        "distance": results["distances"][0][i]
                        if results.get("distances")
                        else 0,
                    }
                )

        return formatted

    def delete(
        self,
        ids: Optional[list[str]] = None,
        where: Optional[dict] = None,
    ) -> int:
        if not self.is_ready:
            return 0

        if ids:
            self._collection.delete(ids=ids)
            return len(ids)
        elif where:
            results = self._collection.get(where=where)
            count = len(results.get("ids", []))
            if count > 0:
                self._collection.delete(where=where)
            return count
        return 0

    def update_document(
        self,
        doc_id: str,
        document: str,
        metadata: Optional[dict] = None,
    ):
        if not self.is_ready:
            return

        self._collection.update(
            ids=[doc_id],
            documents=[document],
            metadatas=[metadata] if metadata else None,
        )

    def count(self) -> int:
        if not self.is_ready:
            return 0
        return self._collection.count()

    def get_all(self, limit: int = 100, offset: int = 0) -> list[dict]:
        if not self.is_ready:
            return []

        results = self._collection.get(limit=limit, offset=offset)
        formatted = []
        if results.get("ids"):
            for i, doc_id in enumerate(results["ids"]):
                formatted.append(
                    {
                        "id": doc_id,
                        "document": results["documents"][i]
                        if results.get("documents")
                        else "",
                        "metadata": results["metadatas"][i]
                        if results.get("metadatas")
                        else {},
                    }
                )
        return formatted
