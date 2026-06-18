import requests
from src.finance.domain.exceptions import PayoutAPIError

class PayoutClient:
    def __init__(self, key: str, secret: str, account_number: str):
        self.auth = (key, secret)
        self.account_number = account_number
        self.base_url = "https://api.razorpay.com/v1/payouts"

    def _get_or_create_fund_account(self, uid: str) -> str:
        # In a real scenario, this fetches the mapped RazorpayX fund account ID for the user
        # For implementation simplicity, assuming it's available or stored in Firestore
        # Mocking for now:
        return f"fa_mock_{uid}"

    def create_payout(self, uid: str, amount_paise: int, idempotency_key: str) -> dict:
        payload = {
            "account_number": self.account_number,
            "fund_account_id": self._get_or_create_fund_account(uid),
            "amount": amount_paise,
            "currency": "INR",
            "mode": "IMPS",
            "purpose": "payout",
            "reference_id": idempotency_key
        }
        try:
            resp = requests.post(self.base_url, json=payload, auth=self.auth, headers={
                "X-Payout-Idempotency": idempotency_key
            }, timeout=10)
        except Exception as e:
            raise PayoutAPIError(str(e))
            
        if resp.status_code >= 400:
            raise PayoutAPIError(f"RazorpayX API Error: {resp.text}", raw_response=resp.json())
        return resp.json()
