import collections
import threading


class LRU:
    """Tiny thread-safe LRU for Tidal metadata that rarely changes."""

    def __init__(self, size):
        self._size = size
        self._data = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def put(self, key, value):
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._size:
                self._data.popitem(last=False)
