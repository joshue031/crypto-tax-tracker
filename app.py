import io
import csv

from flask import Flask, render_template, request, redirect, url_for, flash, Response
from datetime import datetime
from sqlalchemy import or_

from config import Config
from vars import etherscan_key, basescan_key, coingecko_key
from models import db, Transaction, Lot, COINGECKO_ASSET_MAPPING
from forms import TransactionForm

from currency_converter import CurrencyConverter

# Initialize currency converter
currency_converter = CurrencyConverter(fallback_on_missing_rate=True)

import requests

def calculate_gains(selected_year=None):
    """
    Calculate gains for all transactions and manage the Lot table.
    """

    # A place to store CSV lines for partial-lot disposals
    disposal_lines = []

    # Step 1: Clear and repopulate lots
    print("Clearing existing lots...")
    Lot.query.delete()  # Deletes all rows in the Lot table
    db.session.commit()

    print("Populating lots for BUY transactions...")
    buy_transactions = Transaction.query.filter_by(transaction_type="BUY").order_by(Transaction.transaction_date).all()
    for buy_tx in buy_transactions:
        print(f"Adding lot for {buy_tx.to_asset} with remaining {buy_tx.to_amount}")
        new_lot = Lot(
            transaction_id=buy_tx.id,
            asset_name=buy_tx.to_asset,
            remaining_amount=buy_tx.to_amount,
            buy_price=buy_tx.to_asset_cost_basis,
            transaction_date=buy_tx.transaction_date
        )
        db.session.add(new_lot)
    db.session.commit()

    # Step 2: Process all SELL, SWAP, and gas-related transactions
    transactions = Transaction.query.order_by(Transaction.transaction_date).all()
    for tx in transactions:

        # Compute the USD->EUR conversion rate
        conversion_rate = currency_converter.convert(1.0, "USD", "EUR", date=tx.transaction_date)

        # Process SELL or SWAP transactions for asset gains
        if tx.transaction_type in ["SELL", "SWAP"]:
    
            sell_amount = tx.from_amount
            short_term_gains = 0
            long_term_gains = 0
            cost_basis = 0

            # Allocate lots using FIFO
            lots = Lot.query.filter_by(asset_name=tx.from_asset).order_by(Lot.transaction_date).all()
            for lot in lots:
                if sell_amount <= 0:
                    break

                if lot.remaining_amount == 0:
                    continue  # Skip empty lots

                # Determine how much to allocate from this lot
                if sell_amount <= lot.remaining_amount:
                    allocated_amount = sell_amount
                    lot.remaining_amount -= sell_amount
                    sell_amount = 0
                else:
                    allocated_amount = lot.remaining_amount
                    sell_amount -= lot.remaining_amount
                    lot.remaining_amount = 0

                # Calculate overall cost basis
                cost_basis += allocated_amount * lot.buy_price

                # Cost basis for this chunk
                chunk_cost = allocated_amount * lot.buy_price
                # Proceeds for this chunk
                chunk_proceeds = allocated_amount * tx.from_asset_price_usd
                # Gains for this chunk
                chunk_gain = chunk_proceeds - chunk_cost

                # Determine short-term or long-term
                holding_period_days = (tx.transaction_date - lot.transaction_date).days
                is_short = (holding_period_days < 365)

                # If we want to record partial-lot disposal lines:
                if selected_year and tx.tax_year == int(selected_year):
                    # Build CSV line
                    disposal_lines.append(build_csv_line(
                        asset=tx.from_asset,
                        quantity=allocated_amount,
                        date_acquired=lot.transaction_date,
                        date_sold=tx.transaction_date,
                        proceeds=chunk_proceeds,
                        cost_basis=chunk_cost,
                        is_short=is_short
                    ))

                # Update the total short or long term gains.
                if is_short:
                    short_term_gains += allocated_amount * tx.from_asset_price_usd - (allocated_amount * lot.buy_price)
                else:
                    long_term_gains += allocated_amount * tx.from_asset_price_usd - (allocated_amount * lot.buy_price)

            # Handle remaining amount error
            if sell_amount > 0:
                tx.error = "SELL exceeds available BUY lots"
            else:
                # Assign calculated values to the transaction
                proceeds = tx.from_amount * tx.from_asset_price_usd
                #tx.gains_usd = proceeds - cost_basis
                tx.gains_usd_short = short_term_gains
                tx.gains_usd_long = long_term_gains

                # Convert gains to EUR if necessary (assumes conversion rate is available)
                tx.gains_eur_short = short_term_gains * conversion_rate
                tx.gains_eur_long = long_term_gains * conversion_rate

        # Process gas gains for transactions with gas fees in a non-fiat asset
        gas_fees = tx.gas_fees
        if tx.gas_asset and gas_fees > 0:
            print(f"Processing gas gains for {tx.gas_asset}")
            gas_price_usd = tx.gas_asset_price_usd or 0
            proceeds = gas_fees * gas_price_usd
            short_term_gains = 0
            long_term_gains = 0
            cost_basis = 0

            # Allocate lots for the gas asset
            lots = Lot.query.filter_by(asset_name=tx.gas_asset).order_by(Lot.transaction_date).all()
            for lot in lots:
                if gas_fees <= 0:
                    break

                if lot.remaining_amount == 0:
                    continue  # Skip empty lots

                # Determine how much to allocate from this lot
                if gas_fees <= lot.remaining_amount:
                    allocated_amount = gas_fees
                    lot.remaining_amount -= gas_fees
                    gas_fees = 0
                else:
                    allocated_amount = lot.remaining_amount
                    gas_fees -= lot.remaining_amount
                    lot.remaining_amount = 0

                # Calculate cost basis
                cost_basis += allocated_amount * lot.buy_price

                # Cost basis for gas chunk
                chunk_cost = allocated_amount * lot.buy_price
                # Proceeds for gas chunk
                chunk_proceeds = allocated_amount * (tx.gas_asset_price_usd or 0)
                # Gains
                chunk_gain = chunk_proceeds - chunk_cost

                # Determine short-term or long-term gains
                holding_period_days = (tx.transaction_date - lot.transaction_date).days
                is_short = (holding_period_days < 365)
                
                # If we want to record partial-lot disposal lines:
                if selected_year and tx.tax_year == int(selected_year):
                    # Build CSV line for gas disposal
                    disposal_lines.append(build_csv_line(
                        asset=tx.gas_asset,
                        quantity=allocated_amount,
                        date_acquired=lot.transaction_date,
                        date_sold=tx.transaction_date,
                        proceeds=chunk_proceeds,
                        cost_basis=chunk_cost,
                        is_short=is_short
                    ))

                if is_short:
                    short_term_gains += allocated_amount * gas_price_usd - (allocated_amount * lot.buy_price)
                else:
                    long_term_gains += allocated_amount * gas_price_usd - (allocated_amount * lot.buy_price)

            # Handle error if gas fees exceed available lots
            if gas_fees > 0:
                tx.error = "Gas fees exceed available lots for the gas asset."
            else:
                # Assign calculated values to the transaction
                #tx.gains_gas_usd = proceeds - cost_basis
                tx.gains_gas_usd_short = short_term_gains
                tx.gains_gas_usd_long = long_term_gains

                # Convert to EUR if necessary (assumes conversion rate is available)
                tx.gains_gas_eur_short = short_term_gains * conversion_rate
                tx.gains_gas_eur_long = long_term_gains * conversion_rate

        # Save changes to lots and transactions
        db.session.commit()

    # Step 3: After computing everything, if selected_year is set,
    # produce a CSV from the disposal_lines
    if selected_year:
        return build_csv_string(disposal_lines)
    else:
        return None

    print("Gains calculation completed.")

def build_csv_line(asset, quantity, date_acquired, date_sold, proceeds, cost_basis, is_short):
    """
    Return a tuple: (Security Description, Quantity, Date Acquired, Date Sold, Proceeds, Cost Basis, Term)
    """
    security_desc = f"CRYPTO {asset}"
    quantity_str = f"{quantity:.8f}"
    date_acq_str = date_acquired.strftime("%Y-%m-%d")
    date_sold_str = date_sold.strftime("%Y-%m-%d")
    proceeds_str = f"{proceeds:.8f}"
    cost_basis_str = f"{cost_basis:.8f}"
    term_flag = "C" if is_short else "F"  # e.g. "C" for short, "F" for long

    return (security_desc, quantity_str, date_acq_str, date_sold_str, proceeds_str, cost_basis_str, term_flag)


def build_csv_string(disposal_lines):
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Security Description", "Quantity", "Date Acquired", "Date Sold", "Proceeds", "Cost Basis", "Term"])

    for line in disposal_lines:
        writer.writerow(line)

    return output.getvalue()


def update_gains_summary(tax_year):
    transactions = Transaction.query.filter_by(tax_year=tax_year).all()
    short_term_gains = sum(t.gains_usd for t in transactions if t.is_short_term)
    long_term_gains = sum(t.gains_usd for t in transactions if not t.is_short_term)
    staking_rewards = sum(t.to_asset_cost_basis for t in transactions if t.transaction_type == "STAKE")
    airdrops = sum(t.to_asset_cost_basis for t in transactions if t.transaction_type == "CLAIM")
    gas_fees = sum(t.gas_fees for t in transactions)
    
    net_gain = short_term_gains + long_term_gains + staking_rewards + airdrops - gas_fees
    
    # Update or create the summary
    summary = GainsSummary.query.filter_by(tax_year=tax_year).first()
    if not summary:
        summary = GainsSummary(tax_year=tax_year)
        db.session.add(summary)
    summary.total_short_term_gains = short_term_gains
    summary.total_long_term_gains = long_term_gains
    summary.total_staking_rewards = staking_rewards
    summary.total_airdrops = airdrops
    summary.total_gas_fees = gas_fees
    summary.net_gain_usd = net_gain
    db.session.commit()

def fetch_historical_price_range(coin_id, transaction_time, vs_currency="usd"):
    """
    Fetch the closest historical price to a transaction timestamp using CoinGecko's Market Chart Range API.
    :param coin_id: CoinGecko coin ID (e.g., "ethereum").
    :param transaction_time: Datetime object representing the transaction time.
    :param vs_currency: Target currency (e.g., "usd").
    :return: The closest price to the transaction time.
    """
    # Convert transaction time to UNIX timestamp
    transaction_timestamp = int(transaction_time.timestamp())
    
    # Define range: ±1 day to ensure we get relevant data
    range_start = transaction_timestamp - 86400  # 1 day before
    range_end = transaction_timestamp + 86400  # 1 day after
    
    # Make the API request
    if(coin_id in COINGECKO_ASSET_MAPPING):
        gecko_id = COINGECKO_ASSET_MAPPING[coin_id]
    else:
        print(f"Warning: {coin_id} not in COINGECKO_ASSET_MAPPING")
        return 0.0
    url = f"https://api.coingecko.com/api/v3/coins/{gecko_id}/market_chart/range"
    params = {
        "vs_currency": vs_currency,
        "from": range_start,
        "to": range_end,
    }
    response = requests.get(url, params=params)
    if response.status_code != 200:
        raise Exception(f"Error fetching price range: {response.status_code}, {response.text}")
    
    # Parse the response
    data = response.json()
    prices = data.get("prices", [])
    if not prices:
        raise ValueError("No price data found for the given range.")
    
    # Find the closest price
    closest_price = None
    smallest_diff = float("inf")
    for price_data in prices:
        timestamp, price = price_data
        diff = abs(transaction_timestamp - (timestamp // 1000))  # Convert ms to seconds
        if diff < smallest_diff:
            smallest_diff = diff
            closest_price = price

    print(f"[fetch_historical_price_range] Returning {closest_price} for {coin_id}")
    return closest_price

def fetch_etherscan_transactions(address, api_key, start_block=0, end_block=99999999):
    url = f"https://api.etherscan.io/api"
    params = {
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": start_block,
        "endblock": end_block,
        "sort": "asc",
        "apikey": api_key
    }

    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        if data["status"] == "1":
            return data["result"]
        else:
            return []
    else:
        raise Exception(f"Error fetching data: {response.status_code}")
    
def fetch_basescan_transactions(address, api_key, start_block=0, end_block=99999999):
    url = "https://api.basescan.org/api"
    params = {
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": start_block,
        "endblock": end_block,
        "sort": "asc",
        "apikey": api_key
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        if data["status"] == "1":
            return data["result"]
        else:
            return []
    else:
        raise Exception(f"Error fetching data: {response.status_code}")

    
def import_kraken_csv(file_path):
    """
    Import transactions from a Kraken CSV file and handle FROM and TO assets based on transaction type.
    :param file_path: Path to the Kraken CSV file.
    """
    with open(file_path, mode="r") as file:
        reader = csv.DictReader(file)
        for row in reader:
            # Parse the pair and skip unsupported pairs
            pair = row["pair"]
            
            # Split the pair into FROM and TO assets
            try:
                asset1, asset2 = pair.split("/")  # e.g., "XRP/EUR" -> ("XRP", "EUR")
            except ValueError:
                print(f"Invalid pair format: {pair}")
                continue
            
            if((asset1 == "USD" or asset1 == "EUR") and (asset2 == "USD" or asset2 == "EUR")):
                print(f"SKIPPING FIAT PAIR ({asset1},{asset2})")
                continue

            transaction_type = row["type"].lower()
            transaction_date = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S.%f")
            
            if transaction_type == "buy":
                from_asset, to_asset = asset2, asset1  # Buying asset1 with asset2
                from_amount = float(row["cost"])
                to_amount = float(row["vol"])
                fee_asset = from_asset

                # Determine the from_asset price (note: for now assuming only fiat buys/sells in Kraken)
                if from_asset == "EUR":
                    from_asset_price_usd = currency_converter.convert(1.0, "EUR", "USD", date=transaction_date)
                    from_asset_price_eur = 1.0
                    to_asset_cost_basis_usd = currency_converter.convert(float(row["price"]), "EUR", "USD", date=transaction_date)
                elif from_asset == "USD":
                    from_asset_price_usd = 1.0
                    from_asset_price_eur = currency_converter.convert(1.0, "USD", "EUR", date=transaction_date)
                    to_asset_cost_basis_usd = float(row["price"])
                else:
                    print(f"INVALID FROM ASSET IN BUY {pair}")
                    from_asset_price_usd = 0.0
                    from_asset_price_eur = 0.0

            elif transaction_type == "sell":
                from_asset, to_asset = asset1, asset2  # Selling asset1 for asset2
                from_amount = float(row["vol"])
                to_amount = float(row["cost"])
                fee_asset = to_asset

                if to_asset == "EUR":
                    from_asset_price_usd = currency_converter.convert(float(row["price"]), "EUR", "USD", date=transaction_date)
                    from_asset_price_eur = float(row["price"])
                    to_asset_cost_basis_usd = currency_converter.convert(1.0, "EUR", "USD", date=transaction_date)
                elif to_asset == "USD":
                    from_asset_price_usd = float(row["price"])
                    from_asset_price_eur = currency_converter.convert(float(row["price"]), "USD", "EUR", date=transaction_date)
                    to_asset_cost_basis_usd = 1.0
                else:
                    print(f"INVALID FROM ASSET IN BUY {pair}")
                    from_asset_price_usd = 0.0
                    from_asset_price_eur = 0.0
            else:
                print("WARNING: Unsupported transaction type")
                continue  # Unsupported transaction type
            
            # Create a new Transaction object
            tx = Transaction(
                from_asset=from_asset,
                to_asset=to_asset,
                from_amount=from_amount,
                from_asset_price_usd=from_asset_price_usd,
                from_asset_price_eur=from_asset_price_eur,
                to_amount=to_amount,
                to_asset_cost_basis=to_asset_cost_basis_usd,
                transaction_type=transaction_type.upper(),
                transaction_date=transaction_date,
                gas_fees=float(row["fee"]),
                gas_asset=fee_asset,  # Assuming fees are paid in the FROM asset
                tax_year=datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S.%f").year
            )
            db.session.add(tx)

        # Commit all transactions to the database
        db.session.commit()
        print("Kraken transactions imported successfully!")

def detect_errors(tx: Transaction):
    """
    Function to detect errors in a transaction, such as a SELL with no prior BUY 
    for the same asset or insufficient holdings for the SELL.
    """
    if tx.transaction_type == "SELL":
        # Check if there's any BUY transaction for the FROM asset prior to this date
        buy_txs = Transaction.query.filter(
            Transaction.to_asset == tx.from_asset,
            Transaction.transaction_type == "BUY",
            Transaction.transaction_date <= tx.transaction_date
        ).all()

        if not buy_txs:
            return f"Error: SELL transaction for {tx.from_asset} before any BUY."

        # Calculate total bought and total sold up to the current transaction date
        total_bought = sum(b.to_amount for b in buy_txs)
        sell_txs = Transaction.query.filter(
            Transaction.from_asset == tx.from_asset,
            Transaction.transaction_type == "SELL",
            Transaction.transaction_date <= tx.transaction_date,
            Transaction.id != tx.id  # Exclude the current transaction
        ).all()
        total_sold = sum(s.from_amount for s in sell_txs)

        # Check if the total holdings are sufficient for this SELL
        if total_bought < (total_sold + tx.from_amount):
            return f"Error: SELL amount exceeds total available holdings for {tx.from_asset}."
    
    return ""  # No error found


app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    db.create_all()  # Create tables if not exist (for demo)


@app.route("/")
def index():
    
    # Get query parameters for filtering
    asset_filter = request.args.get("asset")
    chain_filter = request.args.get("chain")

    # Build the query dynamically based on the filters
    query = Transaction.query
    if asset_filter:
        query = query.filter(
            or_(
                Transaction.from_asset == asset_filter,
                Transaction.to_asset == asset_filter
            )
        )
    if chain_filter:
        query = query.filter(Transaction.chain == chain_filter)

    # Execute the query and order by transaction date
    transactions = query.order_by(Transaction.transaction_date).all()
    
    tx_list = []
    for t in transactions:
        error_msg = detect_errors(t)
        tx_list.append({
            "id": t.id,
            "chain": t.chain,
            "from_asset": t.from_asset,
            "from_amount": t.from_amount,
            "to_asset": t.to_asset,
            "to_amount": t.to_amount,
            "to_asset_cost_basis": t.to_asset_cost_basis,
            "from_asset_price_usd": t.from_asset_price_usd,
            "transaction_type": t.transaction_type,
            "transaction_date": t.transaction_date,
            "tax_year": t.tax_year,
            "gas_fees": t.gas_fees,
            "gas_asset": t.gas_asset,
            "gas_asset_price_usd": t.gas_asset_price_usd,
            "gains_usd_short": t.gains_usd_short,
            "gains_eur_short": t.gains_eur_short,
            "gains_usd_long": t.gains_usd_long,
            "gains_eur_long": t.gains_eur_long,
            "gains_gas_usd_short": t.gains_gas_usd_short,
            "gains_gas_eur_short": t.gains_gas_eur_short,
            "gains_gas_usd_long": t.gains_gas_usd_long,
            "gains_gas_eur_long": t.gains_gas_eur_long,
            "error": error_msg,
            "note": t.note
        })
        
    return render_template("index.html", transactions=tx_list, asset_filter=asset_filter)

@app.route("/import_kraken", methods=["POST"])
def import_kraken():
    """
    Handle the upload and processing of a Kraken CSV file.
    """
    if "kraken_csv" not in request.files:
        flash("No file uploaded. Please select a CSV file.", "danger")
        return redirect(url_for("index"))

    file = request.files["kraken_csv"]

    if file.filename == "":
        flash("No selected file. Please choose a CSV file to upload.", "danger")
        return redirect(url_for("index"))

    if not file.filename.endswith(".csv"):
        flash("Invalid file type. Please upload a CSV file.", "danger")
        return redirect(url_for("index"))

    try:
        # Save the uploaded file temporarily
        file_path = f"/tmp/{file.filename}"
        file.save(file_path)

        # Import the transactions from the CSV file
        import_kraken_csv(file_path)
        flash("Kraken transactions imported successfully.", "success")

    except Exception as e:
        flash(f"Error importing transactions: {str(e)}", "danger")

    return redirect(url_for("index"))

@app.route("/add", methods=["GET", "POST"])
def add_transaction():
    form = TransactionForm()
    if form.validate_on_submit():
        try:
            # Create a new transaction
            tx = Transaction(
                from_asset=form.from_asset.data,
                from_amount=form.from_amount.data,
                from_asset_price_usd=form.from_asset_price_usd.data,
                from_asset_price_eur=form.from_asset_price_eur.data,
                to_asset=form.to_asset.data,
                to_amount=form.to_amount.data,
                to_asset_cost_basis=form.to_asset_cost_basis.data,
                transaction_type=form.transaction_type.data,
                chain=form.chain.data,
                transaction_date=form.transaction_date.data,
                gas_fees=form.gas_fees.data,
                gas_asset=form.gas_asset.data,
                gas_asset_price_usd=form.gas_asset_price_usd.data,
                tax_year=form.transaction_date.data.year,
                note=form.note.data
            )

            # Add and commit the transaction to the database
            db.session.add(tx)
            db.session.commit()

            # Flash success message
            flash("Transaction added successfully.", "success")
            return redirect(url_for("index"))
        except Exception as e:
            # Handle any exceptions (e.g., database errors)
            db.session.rollback()
            flash(f"Error adding transaction: {str(e)}", "danger")
    
    # Render the form template
    return render_template("add_transaction.html", form=form)


@app.route("/edit/<int:tx_id>", methods=["GET", "POST"])
def edit_transaction(tx_id):
    tx = Transaction.query.get_or_404(tx_id)
    form = TransactionForm(obj=tx)
    
    if form.validate_on_submit():
        tx.from_asset = form.from_asset.data
        tx.from_amount = form.from_amount.data if form.from_amount.data is not None else tx.from_amount
        tx.from_asset_price_usd = form.from_asset_price_usd.data or tx.from_asset_price_usd
        tx.from_asset_price_eur = form.from_asset_price_eur.data or tx.from_asset_price_eur
        tx.to_asset = form.to_asset.data or tx.to_asset
        tx.to_amount = form.to_amount.data if form.to_amount.data is not None else tx.to_amount
        tx.to_asset_cost_basis = form.to_asset_cost_basis.data if form.to_asset_cost_basis.data is not None else tx.to_asset_cost_basis
        tx.transaction_type = form.transaction_type.data
        tx.chain = form.chain.data
        tx.transaction_date = form.transaction_date.data
        tx.gas_fees = form.gas_fees.data if form.gas_fees.data is not None else tx.gas_fees
        tx.gas_asset = form.gas_asset.data or tx.gas_asset
        tx.gas_asset_price_usd = form.gas_asset_price_usd.data if form.gas_asset_price_usd.data is not None else tx.gas_asset_price_usd
        tx.note = form.note.data
        
        db.session.commit()
        flash("Transaction updated successfully.", "success")
        return redirect(url_for("index"))
    
    return render_template("edit_transaction.html", form=form, tx_id=tx.id)


@app.route("/delete/<int:tx_id>", methods=["POST"])
def delete_transaction(tx_id):
    tx = Transaction.query.get_or_404(tx_id)
    db.session.delete(tx)
    db.session.commit()
    flash("Transaction deleted.", "info")
    return redirect(url_for("index"))


@app.route("/calculate_gains", methods=["POST"])
def calculate_gains_route():
    selected_year = request.form.get("tax_year", "")
    
    csv_data = calculate_gains(selected_year=selected_year)  # the function returns CSV data or None
    flash("Gains calculated successfully.", "success")

    if selected_year and csv_data:
        filename = f"capgains_{selected_year}.csv"
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )

    return redirect(url_for("index"))


@app.route("/fetch_prices/<int:tx_id>", methods=["POST"])
def fetch_prices(tx_id):
    """
    Fetch historical prices for a single transaction and update the database.
    """
    tx = Transaction.query.get_or_404(tx_id)

    try:
        # Fetch historical prices
        if tx.from_asset:
            from_price_usd = fetch_historical_price_range(tx.from_asset, tx.transaction_date)
            tx.from_asset_price_usd = from_price_usd
        
        if tx.to_asset:
            to_price_usd = fetch_historical_price_range(tx.to_asset, tx.transaction_date)
            tx.to_asset_cost_basis = to_price_usd

        # Save changes
        db.session.commit()
        flash(f"Prices fetched and updated for transaction {tx_id}.", "success")
    except Exception as e:
        flash(f"Error fetching prices: {str(e)}", "danger")
        db.session.rollback()

    return redirect(url_for("index"))

@app.route("/sync_transactions", methods=["POST"])
def sync_transactions():
    eth_address = request.form.get("eth_address")
    api_key_eth = etherscan_key
    base_address = request.form.get("base_address")
    api_key_base = basescan_key

    try:
        # ------------------------------------------------------------------------------------------
        # Fetch transactions from Etherscan
        transactions_eth = fetch_etherscan_transactions(eth_address, api_key_eth)
        print("Got Etherscan transactions")

        for tx in transactions_eth:
            # Filter out non-value transactions
            if int(tx["value"]) >= 0:
                # Determine transaction type (BUY or SELL)
                transaction_type = "BUY" if tx["to"].lower() == eth_address.lower() else "SELL"

                # Fetch historical price (replace with actual API call)
                price_usd = 0 #fetch_historical_price("ethereum", datetime.fromtimestamp(int(tx["timeStamp"])))

                # Create a new transaction
                new_tx = Transaction(
                    chain="ETH",
                    from_asset="ETH",
                    from_amount=int(tx["value"]) / (10**18),
                    from_asset_price_usd=price_usd,
                    transaction_type=transaction_type,
                    transaction_date=datetime.fromtimestamp(int(tx["timeStamp"])),
                    gas_fees=int(tx["gasUsed"]) * int(tx["gasPrice"]) / (10**18),
                    gas_asset="ETH",
                    tax_year=datetime.fromtimestamp(int(tx["timeStamp"])).year,
                )
                db.session.add(new_tx)

                # ------------------------------------------------------------------------------------------
        # Fetch transactions from Etherscan
        transactions_base = fetch_basescan_transactions(base_address, api_key_base)
        print("Got Basescan transactions")

        for tx in transactions_base:
            # Filter out non-value transactions
            if int(tx["value"]) >= 0:
                # Determine transaction type (BUY or SELL)
                transaction_type = "BUY" if tx["to"].lower() == base_address.lower() else "SELL"

                # Fetch historical price (replace with actual API call)
                price_usd = 0 #fetch_historical_price("ethereum", datetime.fromtimestamp(int(tx["timeStamp"])))

                # Create a new transaction
                new_tx = Transaction(
                    chain="BASE",
                    from_asset="ETH",
                    from_amount=int(tx["value"]) / (10**18),
                    from_asset_price_usd=price_usd,
                    transaction_type=transaction_type,
                    transaction_date=datetime.fromtimestamp(int(tx["timeStamp"])),
                    gas_fees=int(tx["gasUsed"]) * int(tx["gasPrice"]) / (10**18),
                    gas_asset="ETH",
                    tax_year=datetime.fromtimestamp(int(tx["timeStamp"])).year,
                )
                db.session.add(new_tx)

        # Commit all transactions to the database
        db.session.commit()
        flash("Transactions synced successfully.", "success")
    except Exception as e:
        # Rollback the session on error
        db.session.rollback()
        flash(f"Error syncing transactions: {str(e)}", "danger")

    return redirect(url_for("index"))

@app.route("/summary")
def summary():
    transactions = Transaction.query.all()
    unsorted_summaries = {}

    for tx in transactions:
        year = tx.tax_year
        if year not in unsorted_summaries:
            unsorted_summaries[year] = {
                "short_term_usd": 0.0,
                "short_term_eur": 0.0,
                "long_term_usd": 0.0,
                "long_term_eur": 0.0,
            }

        short_term_usd = tx.gains_usd_short or 0
        short_term_eur = tx.gains_eur_short or 0
        long_term_usd = tx.gains_usd_long or 0
        long_term_eur = tx.gains_eur_long or 0

        gas_short_term_usd = tx.gains_gas_usd_short or 0
        gas_short_term_eur = tx.gains_gas_eur_short or 0
        gas_long_term_usd = tx.gains_gas_usd_long or 0
        gas_long_term_eur = tx.gains_gas_eur_long or 0

        unsorted_summaries[year]["short_term_usd"] += short_term_usd + gas_short_term_usd
        unsorted_summaries[year]["short_term_eur"] += short_term_eur + gas_short_term_eur
        unsorted_summaries[year]["long_term_usd"] += long_term_usd + gas_long_term_usd
        unsorted_summaries[year]["long_term_eur"] += long_term_eur + gas_long_term_eur

    # Sort the dictionary by year and convert it to a list of tuples: [(year, {data}), ...]
    sorted_summaries = sorted(unsorted_summaries.items(), key=lambda x: x[0])

    return render_template("summary.html", summaries=sorted_summaries)

@app.route("/lots")
def view_lots():
    lots = Lot.query.filter(Lot.remaining_amount > 0).order_by(Lot.asset_name, Lot.transaction_date).all()
    return render_template("lots.html", lots=lots)

@app.route("/lots_collapsed")
def view_lots_collapsed():
    # Query all lots with remaining amounts
    all_lots = Lot.query.filter(Lot.remaining_amount > 0).order_by(Lot.asset_name, Lot.transaction_date).all()

    # Group lots by asset_name
    holdings = {}
    for lot in all_lots:
        asset = lot.asset_name
        if asset not in holdings:
            holdings[asset] = {
                "total_amount": 0.0,
                "lots": []
            }
        holdings[asset]["lots"].append(lot)
        holdings[asset]["total_amount"] += lot.remaining_amount

    return render_template("lots_collapsed.html", holdings=holdings)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
