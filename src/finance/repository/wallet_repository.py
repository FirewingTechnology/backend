from google.cloud import firestore

class WalletRepository:
    def __init__(self, db: firestore.Client):
        self.db = db

    def execute_wallet_credit_tx(self, tx: firestore.Transaction, uid: str, payment_id: str, amount_paise: int):
        wallet_ref = self.db.collection('wallets').document(uid)
        system_transit_ref = self.db.collection('system_wallets').document('system_transit')
        ledger_ref = self.db.collection('users').document(uid).collection('ledger').document()
        processed_ref = self.db.collection('processed_payments').document(payment_id)

        # 1. Idempotency Check
        if processed_ref.get(transaction=tx).exists:
            return "ALREADY_PROCESSED"

        # 2. Wallet Credit (Integer Paise)
        tx.update(wallet_ref, {
            'availableBalance': firestore.Increment(amount_paise),
            'ledgerBalance': firestore.Increment(amount_paise),
            'totalEarned': firestore.Increment(amount_paise),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        # 3. Double-Entry Transit Update
        tx.update(system_transit_ref, {
            'balance': firestore.Increment(-amount_paise)
        })

        # 4. Immutable Ledger Entry
        tx.set(ledger_ref, {
            'id': ledger_ref.id,
            'type': 'CREDIT_WALLET_TOPUP',
            'referenceId': payment_id,
            'netAmount': amount_paise,
            'status': 'settled',
            'timestamp': firestore.SERVER_TIMESTAMP
        })

        # 5. Write Idempotency Marker
        tx.set(processed_ref, {
            'uid': uid,
            'amountPaise': amount_paise,
            'processedAt': firestore.SERVER_TIMESTAMP
        })

        return "PROCESSED"
