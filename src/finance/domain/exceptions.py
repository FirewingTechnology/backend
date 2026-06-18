class InsufficientFundsError(Exception):
    """Raised when a wallet lacks sufficient available balance."""
    pass

class ExternalAPIError(Exception):
    """Raised when a third-party financial API (like RazorpayX) fails."""
    pass

class PayoutAPIError(Exception):
    """Raised specifically when RazorpayX Payout API returns an error."""
    def __init__(self, message, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response
