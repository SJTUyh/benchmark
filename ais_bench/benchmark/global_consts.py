WORKERS_NUM = 0
MAX_CHUNK_SIZE = 2**16
REQUEST_TIME_OUT = None

from logging import DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL = INFO # log level, choose from DEBUG, INFO, WARNING, ERROR, CRITICAL

PENDING_TOKEN_RATE = 2 # 设置越小，过程中降低Request Rate后响应越快，不建议小于1，同时越小可能导致无法及时发送足够请求