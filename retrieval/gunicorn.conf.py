"""gunicorn settings for running the retrieval service in production.

Start it with `gunicorn --config retrieval/gunicorn.conf.py`; the app comes from `wsgi_app`.

Exactly one worker process: the service allows one indexing job at a time and one question or
search at a time, and both rules are kept in that process's memory, so a second process would
break them. The worker runs several threads, so health checks, job status polls, and overview
requests are served while a question holds the lock.

This file is read by gunicorn's main process, which then forks the worker, so it imports only
`retrieval.config` and `retrieval.server_settings`, never `retrieval.service`: that package loads
the whole app, and libraries such as onnxruntime start native threads when imported. Forking a
process that has threads is unsafe, and macOS crashes the forked worker outright.

A worker that stops reporting to gunicorn for `timeout` seconds is restarted; it is set well above
the answer time budget, so a slow question can never look like a hung worker. On shutdown,
requests in progress get `graceful_timeout` seconds to finish; an indexing job still running then
is stopped with the process and marked interrupted at the next start-up, and the person can resume
it from the embedding cache.

No access log is written: the only caller is the Node API, and the error log already carries
every warning and error the service raises.

gunicorn's control socket (on by default since 25.1) is turned off: it lets anyone with access to
the socket change the number of workers, which would break the one-process rule, and the service
has no use for it.
"""

import os

from retrieval.config import (
    SERVICE_GRACEFUL_TIMEOUT_SECONDS,
    SERVICE_WORKER_COUNT,
    SERVICE_WORKER_THREADS,
    SERVICE_WORKER_TIMEOUT_SECONDS,
)
from retrieval.server_settings import read_server_settings

wsgi_app = "retrieval.service:create_app()"

bind = [read_server_settings(os.environ).bind_address]

workers = SERVICE_WORKER_COUNT

worker_class = "gthread"

threads = SERVICE_WORKER_THREADS

timeout = SERVICE_WORKER_TIMEOUT_SECONDS

graceful_timeout = SERVICE_GRACEFUL_TIMEOUT_SECONDS

errorlog = "-"

control_socket_disable = True
