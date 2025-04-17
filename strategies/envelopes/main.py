import datetime
import sys
import asyncio
import ta
from secret import ACCOUNTS
from outils import MEXC  # Utilisation d'un wrapper pour l'API MEXC

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main():
    account = ACCOUNTS["mexc1"]  # Assurez-vous d'utiliser un compte MEXC ici

    margin_mode = "isolated"  # Mode isolé
    leverage = 3
    hedge_mode = True  # Mode hedging (ou non)

    tf = "1h"  # Intervalle de temps (1 heure)
    sl = 0.3  # Stop Loss en pourcentage
    exchange = MEXC(  # Adapter pour MEXC
        public_api=account["public_api"],
        secret_api=account["secret_api"],
    )
    params = {
        "DEAI": {  # Adapter les paires pour MEXC
            "src": "close",
            "ma_base_window": 7,
            "envelopes": [0.1, 0.06, 0.04], #0.1, 0.06, 0.04
            "size": 1,
            "sides": ["long", "short"],
        },
    }

    invert_side = {"long": "sell", "short": "buy"}
    print(
        f"--- Execution started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---"
    )
    try:
        await exchange.load_markets()  # Charger les marchés pour MEXC

        # Fetch and display balances
        balances = await exchange.get_balance()
        usdt_balance = balances["usdt"].total
        deai_balance = balances["deai"].total
        print(f"USDT Balance: {usdt_balance}")
        print(f"DEAI Balance: {deai_balance}")

        pairs = list(params.keys())

        # Adjust position sizes based on balances
        for pair in params:
            params[pair]["size"] = deai_balance *0.15

        # Récupération des données et des indicateurs
        print(f"Getting data and indicators on {len(pairs)} pairs...")
        tasks = [exchange.get_last_ohlcv(pair, tf, 50) for pair in pairs]
        dfs = await asyncio.gather(*tasks)
        df_list = dict(zip(pairs, dfs))

        # Calcul des indicateurs techniques
        for pair in df_list:
            current_params = params[pair]
            df = df_list[pair]
            if df.empty:
                print(f"Data for {pair} is empty, skipping...")
                continue

            if current_params["src"] == "close":
                src = df["close"]
            elif current_params["src"] == "ohlc4":
                src = (df["close"] + df["high"] + df["low"] + df["open"]) / 4

            df["ma_base"] = ta.trend.sma_indicator(
                close=src, window=current_params["ma_base_window"]
            )
            high_envelopes = [
                round(1 / (1 - e) - 1, 3) for e in current_params["envelopes"]
            ]
            for i in range(1, len(current_params["envelopes"]) + 1):
                df[f"ma_high_{i}"] = df["ma_base"] * (1 + high_envelopes[i - 1])
                df[f"ma_low_{i}"] = df["ma_base"] * (
                    1 - current_params["envelopes"][i - 1]
                )

            df_list[pair] = df

        # Récupérer les ordres en attente
        tasks = [exchange.get_open_orders(pair) for pair in pairs]
        print(f"Getting open trigger orders...")
        open_orders = await asyncio.gather(*tasks)
        open_order_list = dict(zip(pairs, open_orders))

        # Cas 1 : 6 ordres (3 buy et 3 sell)
        for pair in pairs:
            orders = open_order_list[pair]
            buy_orders = [order for order in orders if order.side == "BUY"]
            sell_orders = [order for order in orders if order.side == "SELL"]

            # New condition to balance USDT and DEAI
            if len(buy_orders) == 3 and len(sell_orders) == 3:
                row = df_list[pair].iloc[-2]
                new_orders = []
                size = round(deai_balance * 0.2,2)
                if 1.2 * usdt_balance <= deai_balance * row["close"]:
                    print(f"Condition met for balancing: 1.3 * USDT Balance <= DEAI Balance * Close Price")
                    print(f"Executing market sell to balance USDT and DEAI for {pair}")   
                    await exchange.place_market_order(
                        pair=pair,
                        side="sell",
                        size=size,  
                        type="market",
                    )
                    print(f"Market sell executed for {pair}")
                if 0.8 * usdt_balance >= deai_balance * row["close"]:
                    print(f"Condition met for balancing: 1.3 * USDT Balance <= DEAI Balance * Close Price")
                    print(f"Executing market sell to balance USDT and DEAI for {pair}")
                    await exchange.place_market_order(
                        pair=pair,
                        side="buy",
                        size=size,  
                        type="market",
                    )
                    print(f"Market sell executed for {pair}")
                await asyncio.gather(*new_orders)
            if len(orders) > 6:
                print("Trop d'ordres ouverts sur {pair}, on annule tout")
                await exchange.cancel_orders(pair, [order.id for order in orders])
                new_orders = []
                row = df_list[pair].iloc[-2]
                for i in range(3):
                    buy_price = row.get(f"ma_low_{i+1}")
                    sell_price = row.get(f"ma_high_{i+1}")

                    new_orders.append(
                        exchange.place_trigger_order(
                            pair=pair,
                            side="buy",
                            price=buy_price,
                            trigger_price=buy_price * 1.005,
                            size=params[pair]["size"],
                            type="limit",
                        )
                    )
                    new_orders.append(
                        exchange.place_trigger_order(
                            pair=pair,
                            side="sell",
                            price=sell_price,
                            trigger_price=sell_price * 0.995,
                            size=params[pair]["size"],
                            type="limit",
                        )
                    )
                await asyncio.gather(*new_orders)
# Cas : 6 ordres ouverts
            if len(orders) == 6:
                nb_buy = len(buy_orders)
                nb_sell = len(sell_orders)
                if len(buy_orders) == 3:
                        direction = "buy"
                        opposite_direction = "sell"
                else:
                    direction = "sell"
                    opposite_direction = "buy"
                # Récupérer les données M1
                m1_data = await exchange.get_last_ohlcv(pair, "1m", 2)
                last_candle = m1_data.iloc[-1]
                prev_candle = m1_data.iloc[-2]
                ma_base_price = df_list[pair].iloc[-1]["ma_base"]
                
                if nb_buy == nb_sell:     
                    print(f"Case : 6 orders ({nb_buy} buy, {nb_sell} sell) detected for {pair}.")
                    # Supprimer les ordres actuels
                    await exchange.cancel_orders(pair, [order.id for order in orders])

                    # Recréer les 6 ordres de départ
                    new_orders = []
                    row = df_list[pair].iloc[-2]
                    for i in range(3):
                        buy_price = row.get(f"ma_low_{i+1}")
                        sell_price = row.get(f"ma_high_{i+1}")

                        new_orders.append(
                            exchange.place_trigger_order(
                                pair=pair,
                                side="buy",
                                price=buy_price,
                                trigger_price=buy_price * 1.005,
                                size=params[pair]["size"],  # Adjusted size for buy
                                type="limit",
                            )
                        )
                        new_orders.append(
                            exchange.place_trigger_order(
                                pair=pair,
                                side="sell",
                                price=sell_price,
                                trigger_price=sell_price * 0.995,
                                size=params[pair]["size"],  # Adjusted size for sell
                                type="limit",
                            )
                        )
                    await asyncio.gather(*new_orders)
                else : 
                    print(3-min(len(buy_orders), len(buy_orders)), "ordres limit ont été atteint sur {pair}.")              # Supprimer les ordres actuels
                    await exchange.cancel_orders(pair, [order.id for order in orders])
                    new_orders = []
                    row = df_list[pair].iloc[-2]
                    for i in range(3-min(len(buy_orders), len(sell_orders))):
                        new_orders.append(
                            await exchange.place_trigger_order(
                                pair=pair,
                                side=direction,
                                price=ma_base_price,
                                trigger_price=ma_base_price * (0.995 if opposite_direction == "sell" else 1.005),
                                size=params[pair]["size"],
                                type="limit",
                            )
                        )
                       
                    # Placer les ordres de déclenchement
                    for i in range(len(buy_orders)):
                        buy_price = row.get(f"ma_low_{len(buy_orders)-i}") if direction == "buy" else row.get(f"ma_low_{3-(len(buy_orders)-i)}")
                        new_orders.append(
                            await exchange.place_trigger_order(
                                pair=pair,
                                side="buy",
                                price=buy_price,
                                trigger_price=buy_price * 1.005,
                                size=params[pair]["size"],
                                type="limit",
                            )
                        )
                       
                    for i in range(len(sell_orders)):
                        sell_price = row.get(f"ma_high_{i+1}") if direction == "sell" else row.get(f"ma_high_{3-(len(sell_orders)-i)}")
                        new_orders.append(
                            await exchange.place_trigger_order(
                                pair=pair,
                                side="sell",
                                price=sell_price,
                                trigger_price=sell_price * 0.995,
                                size=params[pair]["size"],
                                type="limit",
                            )
                        )
                        
                    await asyncio.gather(*new_orders)
        # Cas : Aucun ordre, création des 6 ordres 
            if len(orders) == 0:
                print("Aucun ordre ouvert sur {pair}, on crée les 6 de départ")
                new_orders = []
                row = df_list[pair].iloc[-2]
                for i in range(3):
                    buy_price = row.get(f"ma_low_{i+1}")
                    sell_price = row.get(f"ma_high_{i+1}")

                    new_orders.append(
                        exchange.place_trigger_order(
                            pair=pair,
                            side="buy",
                            price=buy_price,
                            trigger_price=buy_price * 1.005,
                            size=params[pair]["size"],
                            type="limit",
                        )
                    )
                    new_orders.append(
                        exchange.place_trigger_order(
                            pair=pair,
                            side="sell",
                            price=sell_price,
                            trigger_price=sell_price * 0.995,
                            size=params[pair]["size"],
                            type="limit",
                        )
                    )
                await asyncio.gather(*new_orders)
                print("1")
        # Cas : 3 ordres du même côté et n de l'autre
            if len(orders) <= 5 and (len(buy_orders) == 3 or len(sell_orders) == 3):
                if len(buy_orders) == 3:
                    direction = "buy"
                    opposite_direction = "sell"
                else:
                    direction = "sell"
                    opposite_direction = "buy"
                print(f"Case: {len(orders)} orders ({max(len(buy_orders),len(sell_orders))} {direction} {min(len(buy_orders),len(sell_orders))} {opposite_direction}) detected for {pair}.")

                # Récupérer les données M1
                m1_data = await exchange.get_last_ohlcv(pair, "1m", 2)
                last_candle = m1_data.iloc[-1]
                prev_candle = m1_data.iloc[-2]
                ma_base_price = df_list[pair].iloc[-1]["ma_base"]

                # Vérifier la condition sur les bougies M1
                if (direction == "buy" and (last_candle["low"] < ma_base_price or prev_candle["low"] < ma_base_price)) or (direction == "sell" and (last_candle["high"] > ma_base_price or prev_candle["high"] > ma_base_price)):
                    print(f"TP atteint pour {pair}. Replacement des 6 ordres de départ.")
                    # Supprimer les ordres actuels
                    await exchange.cancel_orders(pair, [order.id for order in orders])

                    # Recréer les 6 ordres de départ
                    new_orders = []
                    row = df_list[pair].iloc[-2]
                    for i in range(3):
                        buy_price = row.get(f"ma_low_{i+1}")
                        sell_price = row.get(f"ma_high_{i+1}")

                        new_orders.append(
                            await exchange.place_trigger_order(
                                pair=pair,
                                side="buy",
                                price=buy_price,
                                trigger_price=buy_price * 1.005,
                                size=params[pair]["size"],
                                type="limit",
                            )
                        )
                        new_orders.append(
                            await exchange.place_trigger_order(
                                pair=pair,
                                side="sell",
                                price=sell_price,
                                trigger_price=sell_price * 0.995,
                                size=params[pair]["size"],
                                type="limit",
                            )
                        )
                    await asyncio.gather(*new_orders)
                else:
                    print(3-min(len(buy_orders), len(sell_orders)), f"ordres limit ont été atteint sur {pair}. Placement de ",3-min(len(buy_orders), len(sell_orders))," TP.")
                    # Supprimer les ordres actuels
                    await exchange.cancel_orders(pair, [order.id for order in orders])
                    new_orders = []
                    row = df_list[pair].iloc[-2]
                    for i in range(3-min(len(buy_orders), len(sell_orders))):
                        await exchange.place_trigger_order(
                            pair=pair,
                            side=direction,
                            price=ma_base_price,
                            trigger_price=ma_base_price * (0.995 if opposite_direction == "sell" else 1.005),
                            size=params[pair]["size"],
                            type="limit",
                        )
                        
                    # Placer les ordres de déclenchement
                    for i in range(len(buy_orders)):
                        buy_price = row.get(f"ma_low_{len(buy_orders)-i}") if direction == "buy" else row.get(f"ma_low_{3-(len(buy_orders)-i)}")
                        if buy_price:
                            new_orders.append(
                                await exchange.place_trigger_order(
                                    pair=pair,
                                    side="buy",
                                    price=buy_price,
                                    trigger_price=buy_price * 1.005,
                                    size=params[pair]["size"],
                                    type="limit",
                                )
                            )
                        
                    for i in range(len(sell_orders)):
                        sell_price = row.get(f"ma_high_{i+1}") if direction == "sell" else row.get(f"ma_high_{3-(len(sell_orders)-i)}")
                        new_orders.append(
                            await exchange.place_trigger_order(
                                pair=pair,
                                side="sell",
                                price=sell_price,
                                trigger_price=sell_price * 0.995,
                                size=params[pair]["size"],
                                type="limit",
                            )
                        )
                        
                    await asyncio.gather(*new_orders)
        
        # Cas : 4 ordres ou moins avec moins de 3 de chaque côté
            if len(orders) <= 4 and len(orders) > 0 and (len(buy_orders) < 3 or len(sell_orders) < 3):
                print(f"Case: 4 or fewer orders with less than 3 on each side detected for {pair}.")
                row = df_list[pair].iloc[-1]
                ma_high_1 = row["ma_high_1"]
                ma_low_1 = row["ma_low_1"]
                current_price = row["close"]

                # Vérifier si le prix est en dehors des limites
                if current_price > ma_high_1 or current_price < ma_low_1:
                    print(f"Price is outside the range of ma_high_1 or ma_low_1 for {pair}. Doing nothing.")
                else:
                    # Calculer la différence entre le nombre d'ordres buy et sell
                    n = len(buy_orders) - len(sell_orders)
                    print(f"Difference between buy and sell orders: n = {n}")

                    # Supprimer les ordres actuels
                    await exchange.cancel_orders(pair, [order.id for order in orders])

                    new_orders = []
                    row = df_list[pair].iloc[-2]

                    if n == 0:
                        print(f"n = 0. Replacing the 6 initial orders for {pair}.")
                        for i in range(3):
                            buy_price = row.get(f"ma_low_{i+1}")
                            sell_price = row.get(f"ma_high_{i+1}")

                            new_orders.append(
                                await exchange.place_trigger_order(
                                    pair=pair,
                                    side="buy",
                                    price=buy_price,
                                    trigger_price=buy_price * 1.005,
                                    size=params[pair]["size"],
                                    type="limit",
                                )
                            )
                            new_orders.append(
                                await exchange.place_trigger_order(
                                    pair=pair,
                                    side="sell",
                                    price=sell_price,
                                    trigger_price=sell_price * 0.995,
                                    size=params[pair]["size"],
                                    type="limit",
                                )
                            )
                        await asyncio.gather(*new_orders) 
                    if abs(n) >= 1:
                        if len(buy_orders) >= len(sell_orders):
                            direction = "buy"
                            opposite_direction = "sell"
                        else:
                            direction = "sell"
                            opposite_direction = "buy"
                        print(f"n = {n}. Placing {abs(n)} orders at ma_base for {pair}.")
                        ma_base_price = row["ma_base"]
                        for i in range(abs(n)):
                            new_orders.append(
                                await exchange.place_trigger_order(
                                    pair=pair,
                                    side=direction,
                                    price=ma_base_price,
                                    trigger_price=ma_base_price * (0.995 if opposite_direction == "sell" else 1.005),
                                    size=params[pair]["size"],
                                    type="limit",
                                )
                            )
                        for i in range(len(buy_orders)):
                            buy_price = row.get(f"ma_low_{len(buy_orders)-i}") if direction == "buy" else row.get(f"ma_low_{3-(len(buy_orders)-i)}")
                            new_orders.append(
                                await exchange.place_trigger_order(
                                    pair=pair,
                                    side="buy",
                                    price=buy_price,
                                    trigger_price=buy_price * 1.005,
                                    size=params[pair]["size"],
                                    type="limit",
                                )
                            )
                        
                        for i in range(len(sell_orders)):
                            sell_price = row.get(f"ma_high_{i+1}") if direction == "sell" else row.get(f"ma_high_{3-(len(sell_orders)-i)}")
                            new_orders.append(
                                await exchange.place_trigger_order(
                                    pair=pair,
                                    side="sell",
                                    price=sell_price,
                                    trigger_price=sell_price * 0.995,
                                    size=params[pair]["size"],
                                    type="limit",
                                )
                            )
                            
                        await asyncio.gather(*new_orders)
                            # placer les ordres restant dans l'ordre decroissant
        await exchange.close()  
        print(
        f"--- Execution finished at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---"
    )         
    except Exception as e:
        print(f"Error in strategy: {e}")
        # Ensure resources are released
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())


# TODO :
# changer les print 
# erreur notif apple 
# serveur et recursivité
