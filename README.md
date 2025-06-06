# PicoCache: Persistent Memoization

**A Persistent, datastore‑backed `lru_cache` for Python.**  
PicoCache gives you the ergonomics of `functools.lru_cache` while keeping your
cached values safe across process restarts and even across machines.  
PicoCache ships with a zero‑dependency **SQLiteCache** that relies only on the
standard‑library `sqlite3` module. Additional back‑ends can be enabled via
_extras_:

- **SQLAlchemyCache** – persists to any SQL database supported by
  [SQLAlchemy](https://www.sqlalchemy.org/).
- **RedisCache** – stores values in [Redis](https://redis.io/), ideal for
  distributed deployments.
- **DjangoCache** – plugs straight into Django’s configured cache backend
  (Memcached, Redis, database, etc.).

---

## Why PicoCache?

- **Familiar API** – decorators feel _identical_ to `functools.lru_cache`.
- **Durable** – survive restarts, scale horizontally.
- **Introspectable** – `cache_info()` and `cache_clear()` just like the
  standard library.
- **Zero boilerplate** – pass a connection URL and start decorating.

---

## Installation

```bash
# core (built‑in SQLiteCache, no external deps)
pip install picocache

# optional extras
pip install picocache[redis]        # RedisCache
pip install picocache[sqlalchemy]   # SQLAlchemyCache
pip install picocache[django]       # DjangoCache
# or any combination, e.g.
pip install "picocache[redis,sqlalchemy]"
```

---

## Quick‑start

### 1. Built‑in SQLiteCache (no external deps)

```python
from picocache import SQLiteCache

cache = SQLiteCache()     # defaults to ./picocache.db

@cache
def fib(n: int) -> int:
    return n if n < 2 else fib(n - 1) + fib(n - 2)
```

### 2. SQLAlchemy back‑end

```python
from picocache import SQLAlchemyCache

# Create the decorator bound to an SQLite file
sql_cache = SQLAlchemyCache("sqlite:///cache.db")

@sql_cache(maxsize=256)        # feels just like functools.lru_cache
def fib(n: int) -> int:
    return n if n < 2 else fib(n - 1) + fib(n - 2)
```

### 3. Redis

```python
from picocache import RedisCache

redis_cache = RedisCache("redis://localhost:6379/0")

@redis_cache(maxsize=128, typed=True)
def slow_add(a: int, b: int) -> int:
    print("Executing body…")
    return a + b
```

On the second call with the same arguments, `slow_add()` returns instantly and
_“Executing body…”_ is **not** printed – the result came from Redis.

### 4. Django

```python
from picocache import DjangoCache

django_cache = DjangoCache()          # uses settings.CACHES["default"]

@django_cache(maxsize=None)           # unlimited size, rely on Django’s TTL
def expensive_fn(x):
    ...
```

---

## Thread Safety

PicoCache is **fully thread-safe**. Multiple threads can safely call decorated functions concurrently without any additional synchronization.

### Key Thread Safety Features:

1. **Atomic Operations**: All cache operations (lookup, store, evict) are atomic and protected by locks.
2. **No Duplicate Computation**: If multiple threads request the same uncached value simultaneously, only one thread computes it while others wait for the result.
3. **Safe Statistics**: Hit/miss counters are updated atomically.
4. **Concurrent Access**: Different threads can safely access different cached values in parallel.

### Backend-Specific Notes:

- **SQLiteCache**: Uses a global lock for all operations. Thread-safe but may become a bottleneck under high concurrency.
- **RedisCache**: Leverages Redis's atomic operations and connection pooling for excellent concurrent performance.
- **SQLAlchemyCache**: Uses SQLAlchemy's connection pooling for thread-safe database access.
- **DjangoCache**: Inherits thread safety from Django's cache framework. When `maxsize` is specified, adds an additional `functools.lru_cache` layer which is also thread-safe.

### Example with Threading:

```python
from picocache import SQLiteCache
import threading

cache = SQLiteCache()

@cache(maxsize=128)
def expensive_computation(x):
    import time
    time.sleep(1)  # Simulate expensive work
    return x * x

# Multiple threads can safely call the cached function
threads = []
for i in range(10):
    t = threading.Thread(target=lambda: expensive_computation(5))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

# Only one thread will perform the computation; others will wait and reuse the result
print(expensive_computation.cache_info())  # Shows hits=9, misses=1
```

---

## API

Each decorator is constructed with connection details (if any) and **called** with
the same signature as `functools.lru_cache`:

```python
SQLAlchemyCache(url_or_engine, *, key_serializer=None, value_serializer=None, ...)
RedisCache(url_or_params, *, key_serializer=None, value_serializer=None, ...)
DjangoCache(*, key_serializer=None, value_serializer=None, ...)
```

### `__call__(maxsize=None, typed=False)`

Returns a decorator that memoises the target function.

| Param     | Type         | Default | Meaning                                                            |
| --------- | ------------ | ------- | ------------------------------------------------------------------ |
| `maxsize` | `int`/`None` | `None`   | Per‑function entry limit (`None` → no limit).                      |
| `typed`   | `bool`       | `False` | Treat arguments with different types as distinct (same as stdlib). |

The wrapped function gains:

- **`.cache_info()`** → `namedtuple(hits, misses, currsize, maxsize)`
- **`.cache_clear()`** or **`.clear()`** → empties the persistent store for that function.

---

## Running the tests

```bash
uv sync --all-extras
just test
```

- SQL tests run against an **in‑memory** SQLite DB (no external services).
- Redis tests are skipped automatically unless a Redis server is available on
  `localhost:6379`.

---

## License

MIT – see [LICENSE](LICENSE) for details.
