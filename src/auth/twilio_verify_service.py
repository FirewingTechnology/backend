import os

class TwilioVerifyService:
    def __init__(self):
        self.account_sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
        self.auth_token = os.environ.get('TWILIO_AUTH_TOKEN', '')
        self.service_sid = os.environ.get('TWILIO_VERIFY_SERVICE_SID', '')
        
        # Read OTP_MODE env var; default to 'twilio' unless explicitly 'mock' or if running in dev without creds
        raw_mode = os.environ.get('OTP_MODE', 'twilio').lower()
        if raw_mode == 'mock' or not (self.account_sid and self.auth_token and self.service_sid):
            self.mode = 'mock'
        else:
            self.mode = 'twilio'

        self.client = None
        if self.mode == 'twilio':
            try:
                from twilio.rest import Client
                self.client = Client(self.account_sid, self.auth_token)
            except Exception as e:
                print(f"[TwilioVerifyService] Failed to initialize Twilio client ({e}). Falling back to mock mode.")
                self.mode = 'mock'

    def send_otp(self, phone: str) -> dict:
        if self.mode == 'mock':
            print(f"[MOCK OTP] Sent SMS code 123456 to {phone}")
            return {"success": True, "message": "OTP Sent"}

        try:
            from twilio.base.exceptions import TwilioRestException
            verification = self.client.verify \
                               .v2 \
                               .services(self.service_sid) \
                               .verifications \
                               .create(to=phone, channel='sms')
            
            if verification.status in ['pending', 'approved']:
                return {"success": True, "message": "OTP Sent"}
            else:
                return {"success": False, "message": f"Twilio status: {verification.status}"}
        except Exception as e:
            print(f"[TwilioVerifyService] send_otp error: {e}")
            # Friendly message, don't expose raw Twilio exception details
            return {"success": False, "message": "Failed to send OTP via SMS. Please try again later."}

    def verify_otp(self, phone: str, code: str) -> dict:
        if self.mode == 'mock':
            if code == '123456':
                return {"success": True, "verified": True}
            return {"success": False, "verified": False, "message": "Invalid OTP code"}

        try:
            verification_check = self.client.verify \
                                     .v2 \
                                     .services(self.service_sid) \
                                     .verification_checks \
                                     .create(to=phone, code=code)
            
            if verification_check.status == 'approved':
                return {"success": True, "verified": True}
            else:
                return {"success": False, "verified": False, "message": "Invalid or expired OTP code"}
        except Exception as e:
            print(f"[TwilioVerifyService] verify_otp error: {e}")
            return {"success": False, "verified": False, "message": "Verification failed. Please request a new OTP."}
