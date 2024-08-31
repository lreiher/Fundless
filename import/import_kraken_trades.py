#!/usr/bin/env python3
import os
import csv

f = "kraken-trades.csv"
data = []


with open(f, "r") as file:
  reader = csv.DictReader(file)
  for line in reader:
    if int(line["datetime"][:4]) < 2019:
      continue
    if line["symbol"].split("/")[1] != "EUR":
      continue
    if line["side"] != "buy":
      continue
    if float(line["cost"]) > 50:
      continue
    print(line["datetime"] + " / " + line["description"])
    d = {
      "date": line["datetime"].replace("T"," "),
      "id": line["id"],
      "buy_symbol": line["symbol"].split("/")[0],
      "sell_symbol": line["symbol"].split("/")[1],
      "price": float(line["price"]),
      "amount": float(line["amount"]),
      "cost": float(line["cost"]),
      "fee": float(line["fee.cost"]),
      "fee_symbol": line["fee.currency"],
      "cost_total": float(line["cost"]) + float(line["fee.cost"]),
      "cost_eur": float(line["fiat.amount"]) + float(line["fiat.fee"]),
      "exchange": "kraken",
    }
    data.append(d)

with open("output.csv", "w") as file:
  writer = csv.DictWriter(file, data[0].keys())
  writer.writeheader()
  writer.writerows(data)

