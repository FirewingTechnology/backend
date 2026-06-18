import time
import uuid
from contextlib import contextmanager
import redis
from .exceptions import LockAcquisitionError

class RedisLockService:
    def __init__(self, redis_client: redis.Redis):
        self.client = redis_client

    @contextmanager
    def acquire(self, lock_key: str, expire_seconds: int = 15):
        lock_token = str(uuid.uuid4())
        acquired = self.client.set(lock_key, lock_token, nx=True, ex=expire_seconds)
        
        if not acquired:
            raise LockAcquisitionError(f"Failed to acquire lock for {lock_key}")
            
        try:
            yield lock_token
        finally:
            script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            self.client.eval(script, 1, lock_key, lock_token)

    def acquire_lock(self, lock_key: str, ttl_seconds: int = 15) -> str:
        lock_token = str(uuid.uuid4())
        acquired = self.client.set(lock_key, lock_token, nx=True, ex=ttl_seconds)
        
        if not acquired:
            raise LockAcquisitionError(f"Failed to acquire lock for {lock_key}")
            
        return lock_token

    def release_lock(self, lock_key: str, lock_token: str) -> bool:
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = self.client.eval(script, 1, lock_key, lock_token)
        return bool(result)
