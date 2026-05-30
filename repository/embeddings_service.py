"""
Voyage AI Embeddings Service
Generates vector embeddings for document chunks using Voyage AI Law-2 model
Falls back to semantic mock embeddings for testing/demo
"""
import json
import logging
import os
from typing import List, Optional, Dict
from django.conf import settings
import numpy as np

try:
    import voyageai  # type: ignore
except Exception:  # pragma: no cover
    voyageai = None

logger = logging.getLogger(__name__)


_VOYAGE_AUTH_DISABLED = False


class SemanticMockEmbeddings:
    """Generate mock embeddings that are semantically correlated
    
    Uses TF-IDF-like approach: words that appear more frequently in text
    contribute more strongly to the embedding direction
    """
    
    KEYWORD_EMBEDDINGS = {
        'confidential': np.array([0.9, 0.1, 0.0, 0.0, 0.0]),
        'confidentiality': np.array([0.85, 0.15, 0.0, 0.05, 0.0]),
        'data': np.array([0.1, 0.8, 0.2, 0.0, 0.1]),
        'protection': np.array([0.2, 0.7, 0.15, 0.0, 0.05]),
        'payment': np.array([0.0, 0.0, 0.0, 0.95, 0.1]),
        'termination': np.array([0.05, 0.05, 0.05, 0.1, 0.85]),
        'liability': np.array([0.1, 0.1, 0.1, 0.1, 0.7]),
        'breach': np.array([0.2, 0.2, 0.2, 0.2, 0.8]),
    }
    
    @staticmethod
    def get_semantic_embedding(text: str, dimension: int = 1024) -> List[float]:
        """
        Generate semantically meaningful embedding based on text content
        
        Args:
            text: Text to embed
            dimension: Embedding dimension (default 1024)
        
        Returns:
            Semantic embedding as list of floats
        """
        text_lower = text.lower()
        
        # Create base vector from keyword matches
        base_vector = np.zeros(5)
        
        for keyword, vector in SemanticMockEmbeddings.KEYWORD_EMBEDDINGS.items():
            if keyword in text_lower:
                # Weight by frequency
                frequency = text_lower.count(keyword)
                base_vector += vector * frequency
        
        # Normalize base vector
        if np.linalg.norm(base_vector) > 0:
            base_vector = base_vector / np.linalg.norm(base_vector)
        
        # Expand to full dimension with correlated noise
        full_embedding = np.zeros(dimension)
        full_embedding[:5] = base_vector * np.sqrt(dimension / 5)
        
        # Add correlated noise based on text hash (deterministic but unique per text)
        np.random.seed(hash(text) % (2**32))
        noise = np.random.randn(dimension - 5) * 0.1
        full_embedding[5:] = noise
        
        # Normalize
        norm = np.linalg.norm(full_embedding)
        if norm > 0:
            full_embedding = full_embedding / norm
        
        return full_embedding.tolist()


class VoyageEmbeddingsService:
    """Service for generating embeddings using Voyage AI or mock"""
    
    # Voyage AI model for legal documents
    MODEL = "voyage-law-2"
    EMBEDDING_DIMENSION = 1024
    
    def __init__(self):
        """Initialize Voyage AI client"""
        self.api_key = settings.VOYAGE_API_KEY
        self.client = None
        self.use_mock = False

        voyage_enabled = os.getenv('ENABLE_VOYAGE_EMBEDDINGS', 'False').strip().lower() in (
            '1', 'true', 'yes', 'y', 'on'
        )
        if not voyage_enabled:
            self.use_mock = True
            logger.info("Voyage embeddings are disabled (ENABLE_VOYAGE_EMBEDDINGS is false); using semantic mock")
            return

        global _VOYAGE_AUTH_DISABLED
        if _VOYAGE_AUTH_DISABLED:
            self.use_mock = True
            logger.info("Voyage disabled for this process due to previous auth/config failure; using semantic mock")
            return
        
        if self.api_key and voyageai is not None:
            try:
                self.client = voyageai.Client(api_key=self.api_key)
                logger.info(f"Voyage AI client initialized with model: {self.MODEL}")
            except Exception as e:
                logger.warning(f"Failed to initialize Voyage AI, using semantic mock: {str(e)}")
                self.use_mock = True
        elif self.api_key and voyageai is None:
            logger.warning("Voyage API key is configured but 'voyageai' package is not installed; using semantic mock embeddings")
            self.use_mock = True
        else:
            logger.info("No Voyage API key configured, using semantic mock embeddings")
            self.use_mock = True
    
    def is_available(self) -> bool:
        """Check if Voyage AI is available"""
        return self.client is not None and bool(self.api_key)
    
    def embed_text(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for a single text
        
        Args:
            text: Text to embed
        
        Returns:
            List of floats representing the embedding, or None if failed
        """
        if not text or len(text.strip()) == 0:
            logger.warning("Empty text provided for embedding")
            return None
        
        # Try Voyage AI first
        if self.client and not self.use_mock:
            try:
                text_to_embed = text[:8000]
                response = self.client.embed(
                    [text_to_embed],
                    model=self.MODEL,
                    input_type="document"
                )
                
                if response and response.embeddings:
                    embedding = response.embeddings[0]
                    logger.info(f"Generated embedding from Voyage AI ({len(embedding)} dims)")
                    return embedding
                else:
                    logger.error("Empty response from Voyage AI, falling back to mock")
                    self.use_mock = True
            
            except Exception as e:
                logger.warning(f"Voyage AI failed ({str(e)}), using semantic mock")
                if 'invalid' in str(e).lower() or '401' in str(e):
                    _VOYAGE_AUTH_DISABLED = True
                self.use_mock = True
        
        # Fall back to semantic mock
        if self.use_mock:
            embedding = SemanticMockEmbeddings.get_semantic_embedding(text, self.EMBEDDING_DIMENSION)
            logger.info(f"Generated semantic mock embedding ({len(embedding)} dims)")
            return embedding
        
        return None
    
    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts
        
        Args:
            texts: List of texts to embed
        
        Returns:
            List of embeddings (or None for failed items)
        """
        if not texts:
            return []
        
        # Try Voyage AI first
        if self.client and not self.use_mock:
            try:
                truncated = [t[:8000] if t else "" for t in texts]
                non_empty_indices = [i for i, t in enumerate(truncated) if t.strip()]
                non_empty_texts = [truncated[i] for i in non_empty_indices]
                
                if not non_empty_texts:
                    logger.warning("All texts are empty")
                    return [None] * len(texts)
                
                response = self.client.embed(
                    non_empty_texts,
                    model=self.MODEL,
                    input_type="document"
                )
                
                if response and response.embeddings:
                    # Map embeddings back to original indices
                    result = [None] * len(texts)
                    for i, embedding_idx in enumerate(non_empty_indices):
                        if i < len(response.embeddings):
                            result[embedding_idx] = response.embeddings[i]
                    
                    logger.info(f"Generated {len([e for e in result if e is not None])} embeddings from Voyage AI")
                    return result
                else:
                    logger.error("Empty response from Voyage AI, falling back to mock")
                    self.use_mock = True
            
            except Exception as e:
                logger.warning(f"Voyage AI batch failed ({str(e)}), using semantic mock")
                if 'invalid' in str(e).lower() or '401' in str(e):
                    _VOYAGE_AUTH_DISABLED = True
                self.use_mock = True
        
        # Fall back to semantic mock
        if self.use_mock:
            result = []
            for text in texts:
                if text and text.strip():
                    embedding = SemanticMockEmbeddings.get_semantic_embedding(text, self.EMBEDDING_DIMENSION)
                    result.append(embedding)
                else:
                    result.append(None)
            
            logger.info(f"Generated {len([e for e in result if e is not None])} semantic mock embeddings")
            return result
        
        return [None] * len(texts)
    
    def embed_query(self, query: str) -> Optional[List[float]]:
        """
        Generate embedding for a search query
        
        Args:
            query: Query text to embed
        
        Returns:
            List of floats representing the embedding
        """
        if not query or len(query.strip()) == 0:
            logger.warning("Empty query provided for embedding")
            return None
        
        # Try Voyage AI first
        if self.client and not self.use_mock:
            try:
                query_text = query[:2000]
                response = self.client.embed(
                    [query_text],
                    model=self.MODEL,
                    input_type="query"
                )
                
                if response and response.embeddings:
                    embedding = response.embeddings[0]
                    logger.info(f"Generated query embedding from Voyage AI ({len(embedding)} dims)")
                    return embedding
                else:
                    logger.error("Empty response from Voyage AI, falling back to mock")
                    self.use_mock = True
            
            except Exception as e:
                logger.warning(f"Voyage AI query failed ({str(e)}), using semantic mock")
                if 'invalid' in str(e).lower() or '401' in str(e):
                    _VOYAGE_AUTH_DISABLED = True
                self.use_mock = True
        
        # Fall back to semantic mock
        if self.use_mock:
            embedding = SemanticMockEmbeddings.get_semantic_embedding(query, self.EMBEDDING_DIMENSION)
            logger.info(f"Generated semantic mock query embedding ({len(embedding)} dims)")
            return embedding
        
        return None


class EmbeddingCacheService:
    """Service for caching embeddings to reduce API calls"""
    
    def __init__(self):
        """Initialize cache (in-memory for now, can use Redis later)"""
        self.cache = {}
    
    def get(self, text_hash: str) -> Optional[List[float]]:
        """Get cached embedding"""
        return self.cache.get(text_hash)
    
    def set(self, text_hash: str, embedding: List[float]) -> None:
        """Cache an embedding"""
        self.cache[text_hash] = embedding
    
    def clear(self) -> None:
        """Clear cache"""
        self.cache.clear()
