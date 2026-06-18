from firebase_admin import firestore
import datetime
# import smtplib  # For real email dispatch

db = firestore.client()

class ExecutiveAlertEngine:
    """
    Evaluates global system health metrics against defined thresholds
    and dispatches P0 alerts to the executive team.
    """
    
    # Alerting Thresholds
    MAX_ESCROW_LIABILITY_PAISE = 100000000 # 10 Lakhs
    MAX_STUCK_JOBS = 50
    MAX_PENDING_WITHDRAWALS = 100
    
    @staticmethod
    def evaluate_rules():
        alerts_triggered = []
        
        # Pull latest metrics
        doc = db.collection('ops_metrics_realtime').document('global_stats').get()
        if not doc.exists:
            return []
            
        stats = doc.to_dict()
        
        # Rule 1: Escrow Liability
        escrow = stats.get('escrowLiabilityPaise', 0)
        if escrow > ExecutiveAlertEngine.MAX_ESCROW_LIABILITY_PAISE:
            alerts_triggered.append({
                'type': 'FINANCIAL_RISK',
                'severity': 'P0',
                'message': f'Escrow Liability exceeded 10 Lakh threshold: ₹{escrow/100}'
            })
            
        # Rule 2: Stuck Jobs (Platform Health)
        stuck = stats.get('jobsStuck', 0)
        if stuck > ExecutiveAlertEngine.MAX_STUCK_JOBS:
            alerts_triggered.append({
                'type': 'OPERATIONAL_RISK',
                'severity': 'P1',
                'message': f'Elevated number of stuck jobs detected: {stuck}'
            })
            
        # Dispatch
        for alert in alerts_triggered:
            ExecutiveAlertEngine._dispatch_alert(alert)
            
        return alerts_triggered
        
    @staticmethod
    def _dispatch_alert(alert):
        alert['createdAt'] = firestore.SERVER_TIMESTAMP
        alert['status'] = 'triggered'
        
        # Log to Firestore for Dashboard
        db.collection('system_alerts').add(alert)
        
        # In Production: Send via SendGrid or Slack API
        # print(f"DISPATCHING ALERT TO CTO: {alert['message']}")
        
        # Send Push to Exec App via FCM
        from src.infrastructure.firebase.fcm_service import FCMService
        try:
            FCMService.send_to_topic(
                topic="executive_alerts_p0",
                data={"type": "SYSTEM_ALERT", "severity": alert['severity']},
                title=f"⚠️ {alert['severity']} {alert['type']}",
                body=alert['message']
            )
        except Exception:
            pass
