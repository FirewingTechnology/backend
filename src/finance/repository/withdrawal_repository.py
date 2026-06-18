from google.cloud import firestore

class WithdrawalRepository:
    def __init__(self, db: firestore.Client):
        self.db = db

    def lock_funds_tx(self, tx: firestore.Transaction, uid: str, amount_paise: int, idempotency_key: str):
        wallet_ref = self.db.collection('wallets').document(uid)
        withdrawal_ref = self.db.collection('processed_withdrawals').document(idempotency_key)
        ledger_ref = self.db.collection('users').document(uid).collection('ledger').document()

        if withdrawal_ref.get(transaction=tx).exists:
            return "ALREADY_PROCESSED"

        doc = wallet_ref.get(transaction=tx)
        if doc.get('availableBalance') < amount_paise:
            return "INSUFFICIENT_FUNDS"

        tx.update(wallet_ref, {
            'availableBalance': firestore.Increment(-amount_paise),
            'lockedBalance': firestore.Increment(amount_paise),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        tx.set(ledger_ref, {
            'id': ledger_ref.id,
            'type': 'WITHDRAWAL_LOCK',
            'referenceId': idempotency_key,
            'netAmount': -amount_paise,
            'status': 'processing',
            'timestamp': firestore.SERVER_TIMESTAMP
        })

        tx.set(withdrawal_ref, {
            'withdrawalId': idempotency_key,
            'uid': uid,
            'amountPaise': amount_paise,
            'status': 'processing',
            'createdAt': firestore.SERVER_TIMESTAMP
        })
        return "SUCCESS"

    def release_locked_funds_tx(self, tx: firestore.Transaction, uid: str, amount_paise: int, idempotency_key: str, reason: str):
        wallet_ref = self.db.collection('wallets').document(uid)
        withdrawal_ref = self.db.collection('processed_withdrawals').document(idempotency_key)
        ledger_ref = self.db.collection('users').document(uid).collection('ledger').document()

        tx.update(wallet_ref, {
            'availableBalance': firestore.Increment(amount_paise),
            'lockedBalance': firestore.Increment(-amount_paise),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        tx.set(ledger_ref, {
            'id': ledger_ref.id,
            'type': 'WITHDRAWAL_FAILURE',
            'referenceId': idempotency_key,
            'netAmount': amount_paise,
            'status': 'failed',
            'timestamp': firestore.SERVER_TIMESTAMP
        })

        tx.update(withdrawal_ref, {
            'status': 'payout_failed',
            'failureReason': reason,
            'completedAt': firestore.SERVER_TIMESTAMP
        })

    def complete_withdrawal_tx(self, tx: firestore.Transaction, uid: str, amount_paise: int, idempotency_key: str, payout_id: str):
        wallet_ref = self.db.collection('wallets').document(uid)
        system_transit_ref = self.db.collection('system_wallets').document('system_transit')
        withdrawal_ref = self.db.collection('processed_withdrawals').document(idempotency_key)
        ledger_ref = self.db.collection('users').document(uid).collection('ledger').document()

        doc = withdrawal_ref.get(transaction=tx)
        if doc.get('status') == 'payout_success':
            return

        tx.update(wallet_ref, {
            'lockedBalance': firestore.Increment(-amount_paise),
            'ledgerBalance': firestore.Increment(-amount_paise),
            'totalWithdrawn': firestore.Increment(amount_paise),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })
        
        tx.update(system_transit_ref, {
            'balance': firestore.Increment(amount_paise)
        })

        tx.set(ledger_ref, {
            'id': ledger_ref.id,
            'type': 'WITHDRAWAL_SUCCESS',
            'referenceId': payout_id,
            'netAmount': 0,
            'status': 'settled',
            'timestamp': firestore.SERVER_TIMESTAMP
        })

        tx.update(withdrawal_ref, {
            'status': 'payout_success',
            'payoutId': payout_id,
            'completedAt': firestore.SERVER_TIMESTAMP
        })

    def reverse_withdrawal_tx(self, tx: firestore.Transaction, uid: str, amount_paise: int, idempotency_key: str, payout_id: str, reason: str):
        wallet_ref = self.db.collection('wallets').document(uid)
        withdrawal_ref = self.db.collection('processed_withdrawals').document(idempotency_key)
        ledger_ref = self.db.collection('users').document(uid).collection('ledger').document()

        doc = withdrawal_ref.get(transaction=tx)
        if doc.get('status') in ['reversed', 'payout_failed']:
            return

        tx.update(wallet_ref, {
            'availableBalance': firestore.Increment(amount_paise),
            'lockedBalance': firestore.Increment(-amount_paise),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        tx.set(ledger_ref, {
            'id': ledger_ref.id,
            'type': 'WITHDRAWAL_REVERSAL',
            'referenceId': payout_id,
            'netAmount': amount_paise,
            'status': 'reversed',
            'timestamp': firestore.SERVER_TIMESTAMP
        })

        tx.update(withdrawal_ref, {
            'status': 'reversed',
            'failureReason': reason,
            'completedAt': firestore.SERVER_TIMESTAMP
        })
