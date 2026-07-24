import uuid
from flask import Blueprint, request, jsonify, Response, current_app
from src.voice.twilio_voice_service import TwilioVoiceService

voice_api = Blueprint('voice_api', __name__)
twilio_voice = TwilioVoiceService()

def _get_voice_service():
    db = current_app.config.get('FIRESTORE_DB')
    redis_client = current_app.config.get('REDIS_CLIENT')
    from src.voice.voice_service import VoiceService
    return VoiceService(db, redis_client)


@voice_api.route('/calls/start', methods=['POST'])
def start_call():
    try:
        data = request.get_json() or {}
        booking_id = data.get('bookingId')
        caller_id = data.get('callerId')
        callee_id = data.get('calleeId')
        caller_name = data.get('callerName', 'User')

        if not booking_id or not caller_id or not callee_id:
            return jsonify({"success": False, "message": "bookingId, callerId, and calleeId are required."}), 400

        # Validate active booking in Firestore
        db = current_app.config.get('FIRESTORE_DB')
        valid_statuses = ['accepted', 'on_the_way', 'arrived', 'in_progress']

        job_data = None
        real_booking_id = booking_id

        if booking_id and booking_id != 'active_job':
            job_doc = db.collection('job_requests').document(booking_id).get()
            if job_doc.exists:
                job_data = job_doc.to_dict()
                real_booking_id = job_doc.id

        if not job_data:
            # Dynamic lookup for active job involving caller/callee
            docs = db.collection('job_requests').where('status', 'in', valid_statuses).stream()
            for d in docs:
                data_dict = d.to_dict()
                parts = [data_dict.get('userId'), data_dict.get('electricianId')]
                if caller_id in parts or callee_id in parts:
                    job_data = data_dict
                    real_booking_id = d.id
                    break

        if not job_data or job_data.get('status') not in valid_statuses:
            return jsonify({
                "success": False,
                "message": "Calls are strictly allowed ONLY for active jobs (Accepted, On The Way, Arrived, In Progress)."
            }), 403

        # Generate unique callId and Twilio token for identity customer_uid or pro_uid
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        client_identity = f"customer_{caller_id}" if "customer" in caller_name.lower() else f"professional_{caller_id}"
        access_token = twilio_voice.generate_access_token(client_identity)

        voice_service = _get_voice_service()
        call_record = voice_service.create_call(call_id, real_booking_id, caller_id, callee_id, caller_name)

        return jsonify({
            "success": True,
            "callId": call_id,
            "token": access_token,
            "call": call_record
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@voice_api.route('/calls/accept', methods=['POST'])
def accept_call():
    try:
        data = request.get_json() or {}
        call_id = data.get('callId')
        if not call_id:
            return jsonify({"success": False, "message": "callId is required."}), 400

        voice_service = _get_voice_service()
        call_record = voice_service.accept_call(call_id)
        return jsonify({"success": True, "call": call_record}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@voice_api.route('/calls/reject', methods=['POST'])
def reject_call():
    try:
        data = request.get_json() or {}
        call_id = data.get('callId')
        user_id = data.get('userId')
        is_busy = data.get('isBusy', False)
        if not call_id:
            return jsonify({"success": False, "message": "callId is required."}), 400

        voice_service = _get_voice_service()
        call_record = voice_service.reject_call(call_id, rejected_by=user_id, is_busy=is_busy)
        return jsonify({"success": True, "call": call_record}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@voice_api.route('/calls/end', methods=['POST'])
def end_call():
    try:
        data = request.get_json() or {}
        call_id = data.get('callId')
        user_id = data.get('userId')
        status = data.get('status', 'ended')
        if not call_id:
            return jsonify({"success": False, "message": "callId is required."}), 400

        voice_service = _get_voice_service()
        call_record = voice_service.end_call(call_id, ended_by=user_id, status=status)
        return jsonify({"success": True, "call": call_record}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@voice_api.route('/calls/history', methods=['GET'])
def get_call_history():
    try:
        user_id = request.args.get('userId')
        if not user_id:
            return jsonify({"success": False, "message": "userId parameter is required."}), 400

        voice_service = _get_voice_service()
        logs = voice_service.get_history(user_id)
        return jsonify({"success": True, "history": logs}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@voice_api.route('/calls/token', methods=['GET'])
@voice_api.route('/twilio/access-token', methods=['GET'])
def get_access_token():
    try:
        identity = request.args.get('identity') or request.args.get('userId')
        if not identity:
            return jsonify({"success": False, "message": "identity parameter is required."}), 400

        token = twilio_voice.generate_access_token(identity)
        return jsonify({"success": True, "identity": identity, "token": token}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@voice_api.route('/twilio/voice/webhook', methods=['POST'])
@voice_api.route('/twilio/voice-webhook', methods=['POST'])
def voice_webhook():
    to_identity = request.form.get('To') or 'default_client'
    caller_id = request.form.get('From') or 'PowerSupply Voice'
    twiml_xml = twilio_voice.generate_twiml_dial(to_identity, caller_id)
    return Response(twiml_xml, mimetype='text/xml')


@voice_api.route('/twilio/status', methods=['POST'])
@voice_api.route('/twilio/status-callback', methods=['POST'])
def status_callback():
    call_sid = request.form.get('CallSid')
    call_status = request.form.get('CallStatus')
    call_duration = request.form.get('CallDuration')
    print(f"[Twilio Voice Status V4] CallSid: {call_sid}, Status: {call_status}, Duration: {call_duration}")
    return jsonify({"success": True}), 200
