# crypto-tax-tracker
Do your own crypto taxes

Clone and launch with:
```
python app.py
```
Navigate on a web browser to: `http://127.0.0.1:5001`

Currently supports:
- manually adding transactions
- calculation of capital gains/losses (short and long term) using a FIFO method, including gains/losses from gas fees
- Kraken CSV import 
- some limited ability to obtain transactions from ETH and BASE chains (add API key to vars.py)
- can obtain historical price information for some assets from Coingecko (add API key to vars.py)

Made with assistance from AI, including ChatGPT
