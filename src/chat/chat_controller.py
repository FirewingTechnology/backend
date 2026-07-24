from flask import Blueprint, request, jsonify

chat_api = Blueprint('chat_api', __name__)
chat_service = None

def init_chat_api(service):
    global chat_service
    chat_service = service

@chat_api.route('/authorize-room', methods=['POST'])
def authorize_room():
    data = request.get_json() or {}
    booking_id = data.get('bookingId')
    customer_id = data.get('customerId')
    pro_id = data.get('professionalId')

    if not booking_id or not customer_id or not pro_id:
        return jsonify({'error': 'bookingId, customerId, and professionalId are required'}), 400

    try:
        result = chat_service.authorize_and_create_room(booking_id, customer_id, pro_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 403

@chat_api.route('/presence', methods=['POST'])
def set_presence():
    data = request.get_json() or {}
    user_id = data.get('userId')
    status = data.get('status', 'online')
    room_id = data.get('roomId')

    if not user_id:
        return jsonify({'error': 'userId is required'}), 400

    try:
        result = chat_service.set_presence(user_id, status, room_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@chat_api.route('/typing', methods=['POST'])
def set_typing():
    data = request.get_json() or {}
    room_id = data.get('roomId')
    user_id = data.get('userId')
    typing_type = data.get('typingType', 'typing')

    if not room_id or not user_id:
        return jsonify({'error': 'roomId and userId are required'}), 400

    try:
        result = chat_service.set_typing(room_id, user_id, typing_type)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@chat_api.route('/status', methods=['GET'])
def get_chat_status():
    room_id = request.args.get('roomId')
    user_id = request.args.get('userId')
    peer_id = request.args.get('peerId')

    if not room_id or not user_id or not peer_id:
        return jsonify({'error': 'roomId, userId, and peerId are required'}), 400

    try:
        result = chat_service.get_presence_and_typing(room_id, user_id, peer_id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@chat_api.route('/message-notify', methods=['POST'])
def notify_message():
    data = request.get_json() or {}
    room_id = data.get('roomId')
    message_id = data.get('messageId')
    sender_id = data.get('senderId')
    receiver_id = data.get('receiverId')
    message_text = data.get('text', '')
    sender_name = data.get('senderName', 'User')
    msg_type = data.get('type', 'text')

    if not all([room_id, message_id, sender_id, receiver_id]):
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        result = chat_service.notify_message(room_id, message_id, sender_id, receiver_id, message_text, sender_name, msg_type)
        return jsonify(result), 200
    except Exception as e:
        if str(e) == "Rate limit exceeded":
            return jsonify({'error': 'Rate limit exceeded'}), 429
        return jsonify({'error': str(e)}), 500

@chat_api.route('/admin/moderate', methods=['POST'])
def moderate_message():
    data = request.get_json() or {}
    room_id = data.get('roomId')
    message_id = data.get('messageId')
    action = data.get('action')

    if not room_id or not message_id or action != 'delete':
        return jsonify({'error': 'Invalid action or parameters'}), 400

    try:
        result = chat_service.moderate_message(message_id, room_id, action)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
