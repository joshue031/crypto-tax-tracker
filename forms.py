from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, DateTimeField, SelectField, SubmitField, IntegerField, BooleanField, TextAreaField
from wtforms.validators import DataRequired, InputRequired

class TransactionForm(FlaskForm):
    # Required fields
    from_asset = StringField("From Asset", validators=[DataRequired()])
    from_amount = FloatField("From Amount")
    chain = SelectField(
        "Chain", 
        choices=[("EXCH", "EXCH"), ("ETH", "ETH"), ("BASE", "BASE"), 
                 ("COSMOS", "COSMOS"), ("TIA", "TIA")],
        validators=[DataRequired()]
    )
    transaction_type = SelectField(
        "Transaction Type", 
        choices=[("BUY", "BUY"), ("SELL", "SELL"), ("SWAP", "SWAP"), 
                 ("TXFR", "TXFR"), ("CLAIM", "CLAIM"), ("AIRDROP", "AIRDROP"), ("STAKE", "STAKE"),
                 ("APPROVE", "APPROVE")],
        validators=[DataRequired()]
    )
    transaction_date = DateTimeField("Transaction Date", format="%Y-%m-%d %H:%M:%S", validators=[DataRequired()])
    gas_fees = FloatField("Gas Fees")
    gas_asset = StringField("Gas Asset")
    gas_asset_price_usd = FloatField("Gas Asset Price (USD)")

    # Optional fields
    to_asset = StringField("To Asset")
    to_amount = FloatField("To Amount")
    from_asset_price_usd = FloatField("From Asset Price (USD)")
    from_asset_price_eur = FloatField("From Asset Price (EUR)")
    to_asset_cost_basis = FloatField("To Asset Cost Basis (USD)")
    note = TextAreaField("Note")

    # Submit button
    submit = SubmitField("Submit")



