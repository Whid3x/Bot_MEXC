from typing import List
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import time
import itertools
from pydantic import BaseModel
import traceback
from ccxt.base.errors import ExchangeNotAvailable, RequestTimeout, ExchangeError
import aiohttp  # Ajout de l'import pour aiohttp
import hmac
import hashlib
import urllib.parse

class UsdtBalance(BaseModel):
    total: float
    free: float
    used: float

class DeaiBalance(BaseModel):
    total: float
    free: float
    used: float

class Info(BaseModel):
    success: bool
    message: str


class Order(BaseModel):
    id: str
    pair: str
    type: str
    side: str
    price: float
    size: float
    reduce: bool
    filled: float
    remaining: float
    timestamp: int


class TriggerOrder(BaseModel):
    id: str
    pair: str
    type: str
    side: str
    price: float
    trigger_price: float
    size: float
    reduce: bool
    timestamp: int


class Position(BaseModel):
    pair: str
    side: str
    size: float
    usd_size: float
    entry_price: float
    current_price: float
    unrealizedPnl: float
    liquidation_price: float
    margin_mode: str
    leverage: float
    hedge_mode: bool
    open_timestamp: int
    take_profit_price: float
    stop_loss_price: float


class MEXC:
    def __init__(self, public_api=None, secret_api=None):
        mexc_auth_object = {
            "apiKey": public_api,
            "secret": secret_api,
            "enableRateLimit": True,
            "rateLimit": 100,
        }
        if mexc_auth_object["secret"] == None:
            self._auth = False
            self._session = ccxt.mexc()
        else:
            self._auth = True
            self._session = ccxt.mexc(mexc_auth_object)

    async def load_markets(self):
        self.market = await self._session.load_markets()

    async def close(self):
        await self._session.close()

    def ext_pair_to_pair(self, ext_pair) -> str:
        # Ensure the symbol is formatted correctly for MEXC
        if ":USDT" in ext_pair:
            return ext_pair  # Already in the correct format
        return f"{ext_pair}/USDT:USDT"

    def pair_to_ext_pair(self, pair) -> str:
        # Convert back to the external format
        if "/USDT:USDT" in pair:
            return pair.replace("/USDT:USDT", "")
        return pair.replace("/USDT", "")

    def get_pair_info(self, ext_pair) -> str:
        pair = self.ext_pair_to_pair(ext_pair)
        if pair in self.market:
            return self.market[pair]
        else:
            return None

    def amount_to_precision(self, pair: str, amount: float) -> float:
        pair = self.ext_pair_to_pair(pair)
        try:
            return self._session.amount_to_precision(pair, amount)
        except Exception as e:
            return 0

    def price_to_precision(self, pair: str, price: float) -> float:
        pair = self.ext_pair_to_pair(pair)
        return self._session.price_to_precision(pair, price)

    async def get_balance(self) -> dict:
        resp = await self._session.fetch_balance()
        return {
            "usdt": UsdtBalance(
                total=resp["total"]["USDT"],
                free=resp["free"]["USDT"],
                used=resp["used"]["USDT"],
            ),
            "deai": DeaiBalance(
                total=resp["total"].get("DEAI", 0),
                free=resp["free"].get("DEAI", 0),
                used=resp["used"].get("DEAI", 0),
            ),
        }

    async def get_last_ohlcv(self, pair, timeframe, limit=1000) -> pd.DataFrame:
        pair = self.ext_pair_to_pair(pair)
        max_per_request = 200  # Limite MEXC
        ts_dict = {
            "1m": 1 * 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "2h": 2 * 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000,
        }

        all_ohlcv = []
        since = int((time.time() * 1000) - (limit * ts_dict[timeframe]))
        fetched = 0

        while fetched < limit:
            batch_limit = min(max_per_request, limit - fetched)
            ohlcv = await self._session.fetch_ohlcv(
                symbol=pair,
                timeframe=timeframe,
                since=since,
                limit=batch_limit
            )
            if not ohlcv:
                break

            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + ts_dict[timeframe]
            fetched = len(all_ohlcv)

        df = pd.DataFrame(
            all_ohlcv, columns=["date", "open", "high", "low", "close", "volume"]
        )
        df["date"] = pd.to_datetime(df["date"], unit="ms")
        df.set_index("date", inplace=True)
        df = df.sort_index()
        return df


    async def set_margin_mode_and_leverage(self, pair, margin_mode, leverage):
        # MEXC doesn't use the same set_margin_mode functionality. You would typically manage margin through order parameters.
        return Info(success=True, message="Margin mode and leverage not directly configurable via MEXC API.")

    
    async def place_trigger_order(
        self,
        pair,
        side,
        price,
        trigger_price,
        size,
        type,
        reduce=False,
    ) -> Info:
            try:
                # Ensure the symbol is formatted correctly for MEXC
                pair = self.ext_pair_to_pair(pair).replace("/USDT:USDT", "USDT")
                side_mapping = {"buy": "BUY", "sell": "SELL"}
                order_type_mapping = {
                    "limit": "LIMIT",
                    "market": "MARKET",
                }

                if type not in order_type_mapping:
                    raise ValueError(f"Invalid order type: {type}. Expected 'limit' or 'market'.")

                params = {
                    "symbol": pair.replace("/USDT:USDT", ""),  # Format requis pour v3
                    "side": side_mapping[side],
                    "type": order_type_mapping[type],
                    "quantity": size,
                    "price": price,
                    "stopPrice": trigger_price,  # Prix de déclenchement
                    "reduceOnly": reduce,
                    "timestamp": int(time.time() * 1000),  # Current timestamp
                }

                # Generate the signature
                query_string = "&".join([f"{key}={urllib.parse.quote(str(value))}" for key, value in params.items()])
                signature = hmac.new(
                    self._session.secret.encode(),
                    query_string.encode(),
                    hashlib.sha256
                ).hexdigest()
                params["signature"] = signature

                url = f"https://api.mexc.com/api/v3/order?{query_string}&signature={signature}"
                headers = {
                    "Content-Type": "application/json",
                    "X-MEXC-APIKEY": self._session.apiKey,
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers) as response:
                        if response.status != 200:
                            raise ExchangeError(f"HTTP Error {response.status}: {await response.text()}")
                        data = await response.json()
                        print("API response:", data)
                        return Info(success=True, message=f"Trigger Order set up successfully")
            except Exception as e:
                print("Unexpected error occurred:")
                print(traceback.format_exc())
                return Info(success=False, message=f"Unexpected error: {e}")
            
    async def place_market_order(
        self,
        pair,
        side,
        size,
        type,
        reduce=False,
    ) -> Info:
            try:
                # Ensure the symbol is formatted correctly for MEXC
                pair = self.ext_pair_to_pair(pair).replace("/USDT:USDT", "USDT")
                side_mapping = {"buy": "BUY", "sell": "SELL"}
                order_type_mapping = {
                    "limit": "LIMIT",
                    "market": "MARKET",
                }

                params = {
                    "symbol": pair.replace("/USDT:USDT", ""),  # Format requis pour v3
                    "side": side_mapping[side],
                    "type": order_type_mapping[type],
                    "quantity": size,
                    "reduceOnly": reduce,
                    "timestamp": int(time.time() * 1000),  # Current timestamp
                }

                # Generate the signature
                query_string = "&".join([f"{key}={urllib.parse.quote(str(value))}" for key, value in params.items()])
                signature = hmac.new(
                    self._session.secret.encode(),
                    query_string.encode(),
                    hashlib.sha256
                ).hexdigest()
                params["signature"] = signature

                url = f"https://api.mexc.com/api/v3/order?{query_string}&signature={signature}"
                headers = {
                    "Content-Type": "application/json",
                    "X-MEXC-APIKEY": self._session.apiKey,
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers) as response:
                        if response.status != 200:
                            raise ExchangeError(f"HTTP Error {response.status}: {await response.text()}")
                        data = await response.json()
                        print("API response:", data)
                        return Info(success=True, message=f"Trigger Order set up successfully")
            except Exception as e:
                print("Unexpected error occurred:")
                print(traceback.format_exc())
                return Info(success=False, message=f"Unexpected error: {e}")

    async def get_open_orders(self, pair) -> List[Order]:
        try:
            pair = self.ext_pair_to_pair(pair).replace("/USDT:USDT", "USDT")
            url = f"https://api.mexc.com/api/v3/openOrders"
            params = {
                "symbol": pair,
                "timestamp": int(time.time() * 1000),  # Current timestamp
            }

            # Generate the signature
            query_string = "&".join([f"{key}={urllib.parse.quote(str(value))}" for key, value in params.items()])
            signature = hmac.new(
                self._session.secret.encode(),
                query_string.encode(),
                hashlib.sha256
            ).hexdigest()
            params["signature"] = signature

            headers = {
                "Content-Type": "application/json",
                "X-MEXC-APIKEY": self._session.apiKey,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status != 200:
                        raise ExchangeError(f"HTTP Error {response.status}: {await response.text()}")
                    data = await response.json()
                    return [
                        Order(
                            id=order["orderId"],
                            pair=self.pair_to_ext_pair(order["symbol"]),
                            type=order["type"],
                            side=order["side"],
                            price=float(order["price"]),
                            size=float(order["origQty"]),
                            reduce=order.get("reduceOnly", False),
                            filled=float(order["executedQty"]),
                            remaining=float(order["origQty"]) - float(order["executedQty"]),
                            timestamp=int(order["time"]),
                        )
                        for order in data
                    ]
        except Exception as e:
            print("Unexpected error occurred while fetching open orders:")
            print(traceback.format_exc())
            return []

    async def get_order_by_id(self, order_id, pair) -> Order:
        pair = self.ext_pair_to_pair(pair)
        resp = await self._session.fetch_order(order_id, pair)
        return Order(
            id=resp["id"],
            pair=self.pair_to_ext_pair(resp["symbol"]),
            type=resp["type"],
            side=resp["side"],
            price=resp["price"],
            size=resp["amount"],
            reduce=resp["reduceOnly"],
            filled=resp["filled"],
            remaining=resp["remaining"],
            timestamp=resp["timestamp"],
        )

    async def cancel_orders(self, pair, ids=[]):
        try:
            pair = self.ext_pair_to_pair(pair).replace("/USDT:USDT", "USDT")
            url = f"https://api.mexc.com/api/v3/openOrders"
            params = {
                "symbol": pair,
                "timestamp": int(time.time() * 1000),  # Current timestamp
            }

            # Generate the signature
            query_string = "&".join([f"{key}={urllib.parse.quote(str(value))}" for key, value in params.items()])
            signature = hmac.new(
                self._session.secret.encode(),
                query_string.encode(),
                hashlib.sha256
            ).hexdigest()
            params["signature"] = signature

            headers = {
                "Content-Type": "application/json",
                "X-MEXC-APIKEY": self._session.apiKey,
            }

            async with aiohttp.ClientSession() as session:
                async with session.delete(url, params=params, headers=headers) as response:
                    if response.status != 200:
                        raise ExchangeError(f"HTTP Error {response.status}: {await response.text()}")
                    data = await response.json()
                    # Debugging: Print the response to ensure it's being handled correctly
                    print(f"Debug: Cancel orders response: {data}")
                    return Info(success=True, message=f"All trigger orders cancelled: {data}")
        except Exception as e:
            # Debugging: Print the exception to identify the issue
            print(f"Debug: Exception in cancel_orders: {e}")
            return Info(success=False, message=f"Error cancelling trigger orders: {e}")
