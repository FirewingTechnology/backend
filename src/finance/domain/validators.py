from marshmallow import Schema, fields, validate, ValidationError

# Global Max Transaction Limit (e.g., 5,00,000 INR = 50,000,000 Paise)
MAX_TX_LIMIT = 50000000 

class FinancialTransactionSchema(Schema):
    """
    Validates financial input payloads to prevent negative balance injection, 
    overflow attacks, and corrupt escrow mathematical functions.
    """
    amountPaise = fields.Int(
        required=True, 
        validate=validate.Range(
            min=1, 
            max=MAX_TX_LIMIT, 
            error="Amount must be a positive integer below the maximum limit."
        )
    )
    userUid = fields.Str(required=True, validate=validate.Length(min=10, max=128))
    category = fields.Str(required=True)
    geohash = fields.Str(required=True, validate=validate.Length(min=4, max=12))
    location = fields.Dict(required=True)
