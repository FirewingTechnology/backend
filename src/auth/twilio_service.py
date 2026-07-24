import os
import random
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

class TwilioService:
    def __init__(self):
        self.account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        self.auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
        self.service_sid = os.environ.get('TWILIO_VERIFY_SERVICE_SID')
        
        self.is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'
        
        if not self.is_dev_mode and (not self.account_sid or not self.auth_token or not self.service_sid):
            print("WARNING: Twilio credentials not found. Ensure TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_VERIFY_SERVICE_SID are set.")
            self.client = None
        elif self.account_sid and self.auth_token:
            self.client = Client(self.account_sid, self.auth_token)
        else:
            self.client = None

    def send_otp(self, phone_number: str) -> dict:
        """
        Sends an OTP to the given phone number via Twilio Verify.
        """
        if self.is_dev_mode or not self.client:
            # Dev mode fallback
            otp = random.randint(100000, 999999)
            print(f"=============================\nDEV MODE: Twilio SMS Bypassed\nOTP for {phone_number} is {otp}\n=============================")
            return {"success": True, "message": "OTP sent in dev mode."}
            
        try:
            verification = self.client.verify \
                               .v2 \
                               .services(self.service_sid) \
                               .verifications \
                               .create(to=phone_number, channel='sms')
            return {"success": True, "message": "OTP sent.", "status": verification.status}
        except TwilioRestException as e:
            return {"success": False, "message": e.msg, "code": e.code}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def verify_otp(self, phone_number: str, code: str) -> dict:
        """
        Verifies the given OTP code for the phone number.
        """
        if self.is_dev_mode or not self.client:
            if code == '123456':
                return {"success": True, "status": "approved"}
            return {"success": False, "message": "Invalid mock OTP"}

        try:
            verification_check = self.client.verify \
                                     .v2 \
                                     .services(self.service_sid) \
                                     .verification_checks \
                                     .create(to=phone_number, code=code)
            
            if verification_check.status == 'approved':
                return {"success": True, "status": "approved"}
            elif verification_check.status == 'pending':
                return {"success": False, "message": "Invalid OTP.", "status": "pending"}
            else:
                return {"success": False, "message": f"Verification status: {verification_check.status}", "status": verification_check.status}
        except TwilioRestException as e:
            return {"success": False, "message": e.msg, "code": e.code}
        except Exception as e:
            return {"success": False, "message": str(e)}
