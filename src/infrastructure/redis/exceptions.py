class LockAcquisitionError(Exception):
    """Raised when a distributed lock cannot be acquired after maximum retries."""
    pass
