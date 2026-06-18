from locust import HttpUser, task, between
import random
import time

class FinanceStressUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self):
        # Setup mock auth token
        self.headers = {"Authorization": "Bearer MOCK_TOKEN_FOR_LOAD_TEST"}
        self.uid = f"user_{random.randint(1000, 9999)}"

    @task(3)
    def test_withdrawal_race(self):
        payload = {
            "uid": self.uid,
            "amountPaise": 50000, # 500 INR
            "idempotencyKey": f"idem_wd_{int(time.time())}"
        }
        with self.client.post("/api/v2/finance/withdraw", json=payload, headers=self.headers, catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 409:
                response.success() # Lock Acquisition Error is expected in a race
            elif response.status_code == 422:
                response.success() # Insufficient funds is also expected
            else:
                response.failure(f"Unexpected status code: {response.status_code}")

    @task(1)
    def test_job_accept_race(self):
        payload = {
            "jobId": "shared_job_123",
            "proUid": self.uid
        }
        with self.client.post("/api/v2/jobs/accept", json=payload, headers=self.headers, catch_response=True) as response:
            if response.status_code == 200 or response.status_code == 400 or response.status_code == 409:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")
