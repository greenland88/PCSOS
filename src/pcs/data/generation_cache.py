"""Generation-pinned in-memory cache for canonical dataset reads."""
from __future__ import annotations
from threading import RLock
from typing import Any

class GenerationCache:
    def __init__(self): self._items={}; self._lock=RLock()
    @staticmethod
    def key(dataset, ticker, partition, generation_id):
        if not generation_id: raise ValueError("GENERATION_REQUIRED")
        return (str(dataset),str(ticker).upper(),str(partition),str(generation_id))
    def put(self, dataset, ticker, partition, generation_id, value):
        with self._lock: self._items[self.key(dataset,ticker,partition,generation_id)]=value
    def get(self, dataset, ticker, partition, generation_id):
        with self._lock: return self._items.get(self.key(dataset,ticker,partition,generation_id))
    def invalidate_partition(self, dataset, ticker, partition, *, except_generation=None):
        with self._lock:
            for k in list(self._items):
                if k[:3]==(str(dataset),str(ticker).upper(),str(partition)) and k[3]!=except_generation: del self._items[k]
    def clear(self):
        with self._lock: self._items.clear()

__all__=["GenerationCache"]
