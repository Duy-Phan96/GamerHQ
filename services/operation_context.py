"""Request-local read reuse and concise diagnostics; never cache across commands."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
import inspect
import time

log = logging.getLogger('discord.gamerhq.performance')
_metrics = ContextVar('operation_metrics', default=None)
_reads = ContextVar('operation_reads', default=None)


def count(name, amount=1):
    metrics = _metrics.get()
    if metrics is not None: metrics[name] = metrics.get(name, 0) + amount


@contextmanager
def operation(name):
    if _metrics.get() is not None:
        yield _metrics.get()
        return
    metrics = dict(api_reads=0, discord_api_calls=0, history_scans=0, db_queries=0, rate_limits=0)
    token = _metrics.set(metrics)
    started = time.perf_counter()
    try:
        yield metrics
    finally:
        log.info('operation=%s duration_ms=%d api_reads=%d discord_api_calls=%d history_scans=%d db_queries=%d rate_limits=%d',
                 name, (time.perf_counter()-started)*1000, *(metrics[k] for k in ('api_reads', 'discord_api_calls', 'history_scans', 'db_queries', 'rate_limits')))
        _metrics.reset(token)


def measured(name):
    def decorate(function):
        if not inspect.iscoroutinefunction(function):
            @wraps(function)
            def run_sync(*args, **kwargs):
                with operation(name): return function(*args, **kwargs)
            return run_sync
        @wraps(function)
        async def run(*args, **kwargs):
            with operation(name): return await function(*args, **kwargs)
        return run
    return decorate


class ReadContext:
    """At most three independent reads. Futures also coalesce concurrent lookups."""
    def __init__(self):
        self.results = {}
        self.gate = asyncio.Semaphore(3)

    async def once(self, key, read):
        if key in self.results: return await asyncio.shield(self.results[key])
        future = asyncio.get_running_loop().create_future()
        # Retrieve failures even if the owning await is cancelled; no background task.
        future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        self.results[key] = future
        try:
            async with self.gate:
                count('api_reads')
                async with asyncio.timeout(45): result = await read()
            future.set_result(result)
            return result
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError): future.cancel()
            else: future.set_exception(exc)
            raise


@contextmanager
def read_scope(*, reuse=False):
    if reuse and _reads.get() is not None:
        yield _reads.get()
        return
    token = _reads.set(ReadContext())
    try: yield _reads.get()
    finally: _reads.reset(token)


async def read_once(key, read):
    context = _reads.get()
    if context is not None: return await context.once(key, read)
    count('api_reads')
    async with asyncio.timeout(45): return await read()


async def fetch_message(channel, message_id):
    return await read_once(('message', channel.id, int(message_id)), lambda: channel.fetch_message(int(message_id)))


def remember_message(message):
    context = _reads.get()
    key = ('message', message.channel.id, message.id)
    if context is not None and key not in context.results:
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        context.results[key] = future


async def gather_reads(jobs):
    """Await every read, including failures; no orphaned work after cancellation."""
    results = await asyncio.gather(*jobs, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException): raise result
    return results


class RateLimitCounter(logging.Filter):
    def filter(self, record):
        if record.levelno >= logging.WARNING and ('rate limit' in record.getMessage().lower() or '429' in record.getMessage()):
            count('rate_limits')
        return True


def install_http_metrics(client):
    """Observe discord.py's request boundary; leave its retry/rate-limit logic intact."""
    if getattr(client, '_gamerhq_metrics', False): return
    original = client.request
    @wraps(original)
    async def request(*args, **kwargs):
        count('discord_api_calls')
        return await original(*args, **kwargs)
    client.request = request
    client._gamerhq_metrics = True
    logger = logging.getLogger('discord.http')
    if not any(isinstance(f, RateLimitCounter) for f in logger.filters): logger.addFilter(RateLimitCounter())
