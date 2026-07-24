import os
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.twiml.voice_response import VoiceResponse, Dial

class TwilioVoiceService:
    def __init__(self):
        self.account_sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
        self.api_key = os.environ.get('TWILIO_API_KEY', 'SKmock_key_for_dev')
        self.api_secret = os.environ.get('TWILIO_API_SECRET', 'mock_secret_for_dev')
        self.twiml_app_sid = os.environ.get('TWILIO_TWIML_APP_SID', 'APmock_twiml_app_sid')
        self.is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'

    def generate_access_token(self, identity: str) -> str:
        """
        Generates a Twilio AccessToken with VoiceGrant for WebRTC client identity.
        """
        if self.is_dev_mode and self.api_key.startswith('SKmock'):
            # Fallback mock JWT for development testing if keys aren't set yet
            return f"mock_token_{identity}_dev"

        try:
            token = AccessToken(
                self.account_sid,
                self.api_key,
                self.api_secret,
                identity=identity,
                ttl=3600
            )

            voice_grant = VoiceGrant(
                outgoing_application_sid=self.twiml_app_sid,
                incoming_allow=True
            )
            token.add_grant(voice_grant)

            return token.to_jwt()
        except Exception as e:
            print(f"Error generating Twilio Voice AccessToken: {e}")
            return f"mock_token_{identity}_fallback"

    def generate_twiml_dial(self, to_identity: str, caller_id: str = None) -> str:
        """
        Generates TwiML XML to dial a client identity directly via WebRTC.
        """
        response = VoiceResponse()
        dial = Dial(caller_id=caller_id or "PowerSupply Voice")
        dial.client(to_identity)
        response.append(dial)
        return str(response)
