from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

COINGECKO_ASSET_MAPPING = {
    "ETH": "ethereum",
    "BTC": "bitcoin",
    "XRP": "ripple",
    "USDT": "tether",
    "USDC": "usd-coin",
    "DOT": "polkadot",
    "ADA": "cardano",
    "ATOM": "cosmos",
    "TIA": "celestia",
    "AERO": "aerodrome"
}

class Lot(db.Model):
    __tablename__ = 'lots'
    
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=False)
    asset_name = db.Column(db.String(20), nullable=False)  # e.g. BTC
    remaining_amount = db.Column(db.Float, nullable=False)  # Unsold amount from this buy
    buy_price = db.Column(db.Float, nullable=False)  # Price at the time of the BUY
    transaction_date = db.Column(db.DateTime, nullable=False)  # Same as the transaction
    
    transaction = db.relationship("Transaction", back_populates="lots")

class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    chain = db.Column(db.String(10), nullable=False, default="EXCH")  # Default to "EXCH" for exchange transactions
    from_asset = db.Column(db.String(20), nullable=False)  # FROM asset (e.g., ETH)
    from_amount = db.Column(db.Float, nullable=False)      # Amount of FROM asset
    from_asset_price_usd = db.Column(db.Float, nullable=False)  # FMV or sell price in USD
    from_asset_price_eur = db.Column(db.Float, nullable=True)   # FMV or sell price in EUR
    
    to_asset = db.Column(db.String(20), nullable=True)     # TO asset (e.g., BTC)
    to_amount = db.Column(db.Float, nullable=True, default=0.0)         # Amount of TO asset
    to_asset_cost_basis = db.Column(db.Float, nullable=True)  # Cost basis of TO asset in USD
    
    transaction_type = db.Column(db.String(10), nullable=False)  # SELL, SWAP, TXFR, etc.
    transaction_date = db.Column(db.DateTime, nullable=False)
    tax_year = db.Column(db.Integer, nullable=True)  # Tax year for this transaction
    
    gas_fees = db.Column(db.Float, nullable=True, default=0.0)  # Gas fees in FROM asset
    gas_asset = db.Column(db.String(20), nullable=True, default="")  # Asset used for gas fees
    gas_asset_price_usd = db.Column(db.Float, nullable=True, default=0.0)  # Price of gas asset in USD
    
    gains_usd_short = db.Column(db.Float, nullable=True)  # Computed capital gains in USD
    gains_eur_short = db.Column(db.Float, nullable=True)  # Computed capital gains in EUR
    gains_usd_long = db.Column(db.Float, nullable=True)  # Computed capital gains in USD
    gains_eur_long = db.Column(db.Float, nullable=True)  # Computed capital gains in EUR
    gains_gas_usd_short = db.Column(db.Float, nullable=True)  # Computed capital gains for gas in USD
    gains_gas_eur_short = db.Column(db.Float, nullable=True)  # Computed capital gains for gas in EUR
    gains_gas_usd_long = db.Column(db.Float, nullable=True)  # Computed capital gains for gas in USD
    gains_gas_eur_long = db.Column(db.Float, nullable=True)  # Computed capital gains for gas in EUR
    
    error = db.Column(db.String(255), nullable=True)  # Errors (e.g., SELL before BUY)
    note = db.Column(db.Text, nullable=True)  # Optional description for the transaction

    lots = db.relationship("Lot", back_populates="transaction", cascade="all, delete-orphan")

class GainsSummary(db.Model):
    __tablename__ = 'gains_summary'
    
    id = db.Column(db.Integer, primary_key=True)
    tax_year = db.Column(db.Integer, nullable=False, unique=True)  # Tax year
    total_short_term_gains = db.Column(db.Float, nullable=False, default=0.0)
    total_long_term_gains = db.Column(db.Float, nullable=False, default=0.0)
    total_staking_rewards = db.Column(db.Float, nullable=False, default=0.0)
    total_airdrops = db.Column(db.Float, nullable=False, default=0.0)
    total_gas_fees = db.Column(db.Float, nullable=False, default=0.0)
    net_gain_usd = db.Column(db.Float, nullable=False, default=0.0)

