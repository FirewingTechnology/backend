import hmac
import hashlib
import os

class WebhookVerifier:
    """
    Cryptographically verifies the authenticity of incoming webhooks 
    from payment gateways (Razorpay, RazorpayX) to prevent malicious payloads.
    """
    @staticmethod
    def verify_razorpay_signature(payload_body: bytes, signature: str) -> bool:
        secret = os.environ.get("RAZORPAYX_WEBHOOK_SECRET")
        if not secret or not signature:
            return False
            
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            payload_body,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_sig, signature)
