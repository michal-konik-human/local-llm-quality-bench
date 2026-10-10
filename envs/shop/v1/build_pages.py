"""Source of envs/shop/v1/pages.json (run: python3 envs/shop/v1/build_pages.py).

A frozen, fictional web: every shop lives under the reserved .example TLD, so no page
can be confused with a real shop, and the snapshot never changes once published.
To change prices, create envs/shop/v2/ instead; v1 must stay byte-identical so old
results remain reproducible.

Snapshot date: 2026-10-08. Every product task has exactly one correct answer, and
the decoys are deliberate traps: shipping cost, used/refurbished items, out-of-stock
offers, wrong variant, stale aggregator prices, foreign currency, discount codes,
and different products with similar names.
"""

import json
import os

P = []


def page(url, title, text, keywords=(), snippet=None):
    P.append({"url": url, "title": title, "keywords": list(keywords), "text": text.strip(),
              **({"snippet": snippet} if snippet else {})})


# --- Task: Sony WH-1000XM6, new, cheapest total incl. delivery -> audiohouse 1 489,00 zł --------
HEAD = ("słuchawki", "headphones", "sony", "wh-1000xm6", "xm6", "bezprzewodowe", "wireless", "anc")
page("https://elektromarket.example/sony-wh-1000xm6",
     "Sony WH-1000XM6 słuchawki bezprzewodowe – ElektroMarket",
     """Sony WH-1000XM6, czarne. Stan: nowy. Cena: 1 499,00 zł. Dostawa: darmowa (kurier).
Dostępność: w magazynie, wysyłka w 24 h. Gwarancja producenta 24 miesiące.""", HEAD)
page("https://tanieaudio.example/wh-1000xm6",
     "Sony WH-1000XM6 – TanieAudio.example",
     """Słuchawki Sony WH-1000XM6. Produkt nowy. Cena: 1 449,00 zł.
Koszt dostawy: kurier 69,00 zł (brak darmowej dostawy dla tego produktu).
Dostępność: dostępny.""", HEAD)
page("https://outlet-dzwiek.example/sony-xm6-uzywane",
     "Sony WH-1000XM6 używane – Outlet Dźwięk",
     """Sony WH-1000XM6, UŻYWANE, stan dobry (drobne rysy na pałąku). Cena: 1 199,00 zł.
Dostawa: 0 zł. Dostępność: 1 sztuka.""", HEAD + ("używane", "used"))
page("https://megasklep.example/sony-wh-1000xm6-czarne",
     "Sony WH-1000XM6 czarne | MegaSklep",
     """Sony WH-1000XM6 (nowe). Cena: 1 479,00 zł. Dostawa: darmowa.
Dostępność: PRODUKT CHWILOWO NIEDOSTĘPNY – brak w magazynie, termin dostawy nieznany.""", HEAD)
page("https://audiohouse.example/sony-wh-1000xm6",
     "Sony WH-1000XM6 – AudioHouse",
     """Sony WH-1000XM6, kolor czarny. Stan: nowy, oryginalnie zapakowany. Cena: 1 489,00 zł.
Dostawa: darmowa. Dostępność: w magazynie (12 szt.).""", HEAD)
page("https://porownaj.example/sony-wh-1000xm6",
     "Porównaj ceny: Sony WH-1000XM6 – Porownaj.example",
     """Zestawienie ofert dla Sony WH-1000XM6. UWAGA: ceny z dnia 2026-09-01, mogą być nieaktualne –
sprawdź cenę w sklepie.
- TanieAudio.example: 1 399,00 zł – https://tanieaudio.example/wh-1000xm6
- ElektroMarket: 1 499,00 zł – https://elektromarket.example/sony-wh-1000xm6
- AudioHouse: 1 519,00 zł – https://audiohouse.example/sony-wh-1000xm6""", HEAD + ("porównanie", "compare", "ceny", "prices"))
page("https://elektromarket.example/sony-wh-1000xm5",
     "Sony WH-1000XM5 słuchawki – ElektroMarket",
     """Sony WH-1000XM5 (poprzednia generacja). Stan: nowy. Cena: 999,00 zł. Dostawa: darmowa.
Dostępność: w magazynie.""", ("słuchawki", "headphones", "sony", "wh-1000xm5", "xm5"))

# --- Task: Samsung Galaxy S26 256 GB, new -> telesklep 3 869,00 zł ------------------------------
PHONE = ("samsung", "galaxy", "s26", "smartfon", "smartphone", "telefon", "phone")
page("https://komorkowo.example/galaxy-s26-128",
     "Samsung Galaxy S26 128 GB – Komórkowo",
     """Samsung Galaxy S26, pamięć 128 GB, kolor grafitowy. Nowy. Cena: 3 499,00 zł. Dostawa: darmowa.
Dostępny.""", PHONE + ("128", "gb"))
page("https://fonmarket.example/samsung-galaxy-s26-256gb",
     "Samsung Galaxy S26 256 GB – FonMarket",
     """Samsung Galaxy S26, 256 GB, nowy. Cena: 3 899,00 zł. Dostawa: darmowa. Dostępność: w magazynie.""",
     PHONE + ("256", "gb"))
page("https://smartdom.example/galaxy-s26",
     "Samsung Galaxy S26 – SmartDom",
     """Samsung Galaxy S26. Specyfikacja: ekran 6,2", pamięć wbudowana: 128 GB, RAM 12 GB. Nowy.
Cena: 3 749,00 zł. Dostawa: darmowa. Dostępny.""", PHONE)
page("https://telesklep.example/s26-256-grafit",
     "Galaxy S26 256 GB grafitowy – TeleSklep",
     """Samsung Galaxy S26, pamięć 256 GB, grafitowy. Stan: nowy. Cena: 3 869,00 zł. Dostawa: 0 zł.
Dostępność: w magazynie.""", PHONE + ("256", "gb"))
page("https://gsm-okazja.example/galaxy-s26-256-refurb",
     "Samsung Galaxy S26 256 GB odnowiony – GSM Okazja",
     """Samsung Galaxy S26 256 GB – ODNOWIONY (refurbished), klasa A, gwarancja sklepu 12 mies.
Cena: 3 299,00 zł. Dostawa: darmowa. Dostępny.""", PHONE + ("256", "gb", "odnowiony", "refurbished"))

# --- Task: Bosch GSR 18V-90 C, body only, delivered to Poland, EUR at 4,25 -> werkzeug 1 109,25 zł
DRILL = ("bosch", "gsr", "18v-90", "wiertarko-wkrętarka", "wkrętarka", "drill", "driver", "professional")
page("https://narzedziownia.example/bosch-gsr-18v-90c-solo",
     "Bosch GSR 18V-90 C solo (bez akumulatora) – Narzędziownia",
     """Bosch Professional GSR 18V-90 C, wersja SOLO: samo urządzenie, bez akumulatorów i ładowarki.
Cena: 1 129,00 zł. Dostawa: 19,99 zł. Dostępność: w magazynie. Wysyłka na terenie Polski.""", DRILL + ("solo",))
page("https://werkzeug-shop.example/bosch-gsr-18v-90c",
     "Bosch GSR 18V-90 C Professional (Solo) – Werkzeug-Shop",
     """Bosch Professional GSR 18V-90 C – Solo / body only (ohne Akku, ohne Ladegerät / no battery, no charger).
Preis / price: 249,00 € . Versand nach Polen / shipping to Poland: 12,00 €. Lieferbar / in stock.
Prices in EUR.""", DRILL + ("solo", "eur", "body"))
page("https://majster.example/bosch-gsr-18v-90c-zestaw",
     "Bosch GSR 18V-90 C zestaw z 2 akumulatorami – Majster",
     """Bosch GSR 18V-90 C w zestawie: 2 akumulatory 4,0 Ah, ładowarka, walizka L-Boxx. Cena: 1 499,00 zł.
Dostawa: darmowa. Dostępny.""", DRILL + ("zestaw", "kit"))
page("https://budmax.example/bosch-gsr-18v-90c",
     "Bosch GSR 18V-90 C solo – BudMax",
     """Bosch GSR 18V-90 C solo (bez akumulatora). Cena: 1 099,00 zł. Dostawa: 15,00 zł.
Dostępność: BRAK W MAGAZYNIE.""", DRILL + ("solo",))
page("https://kursy.example/eur",
     "Kurs euro (EUR) – Kursy.example",
     """Kurs średni EUR: 1 EUR = 4,2500 zł (tabela z dnia 2026-10-08). Exchange rate: 1 EUR = 4.2500 PLN.""",
     ("kurs", "euro", "eur", "pln", "exchange", "rate", "waluta", "currency"))

# --- Task: Kindle Paperwhite 16 GB, cheapest after published discount codes -> ebookshop 656,10 zł
KINDLE = ("kindle", "paperwhite", "czytnik", "e-book", "ebook", "reader", "amazon", "16")
page("https://czytniki.example/kindle-paperwhite-16gb",
     "Kindle Paperwhite 16 GB – Czytniki.example",
     """Amazon Kindle Paperwhite (2026), 16 GB, bez reklam. Nowy. Cena: 699,00 zł. Dostawa: darmowa. Dostępny.""", KINDLE)
page("https://ebookshop.example/paperwhite-2026",
     "Kindle Paperwhite 2026 16 GB – EbookShop",
     """Amazon Kindle Paperwhite (2026), 16 GB. Nowy. Cena: 729,00 zł. Dostawa: darmowa. Dostępny.
PROMOCJA: kod KINDLE10 obniża cenę tego produktu o 10% w koszyku (ważny do 2026-10-31).""", KINDLE + ("kod", "rabat", "promocja"))
page("https://elektromarket.example/kindle-paperwhite",
     "Kindle Paperwhite 16 GB – ElektroMarket",
     """Amazon Kindle Paperwhite (2026) 16 GB. Nowy. Cena: 689,00 zł. Dostawa: 15,00 zł. Dostępny.""", KINDLE)

# --- Task: Garmin Forerunner 975 (does not exist) -> answer: not found ---------------------------
WATCH = ("garmin", "forerunner", "zegarek", "watch", "sportowy", "running")
page("https://sportowo.example/garmin-forerunner-965",
     "Garmin Forerunner 965 – Sportowo",
     """Zegarek Garmin Forerunner 965, czarny. Nowy. Cena: 2 199,00 zł. Dostawa: darmowa. Dostępny.""", WATCH + ("965",))
page("https://biegacz.example/forerunner-970",
     "Garmin Forerunner 970 – Biegacz.example",
     """Garmin Forerunner 970 (model 2026). Nowy. Cena: 2 899,00 zł. Dostawa: darmowa. Dostępny.""", WATCH + ("970",))
page("https://forum-biegowe.example/forerunner-975-plotki",
     "Forerunner 975? Plotki o nowym modelu – Forum Biegowe",
     """Wątek forum: czy Garmin wyda Forerunner 975? Na razie to tylko plotki, producent nic nie ogłosił.
Model nie jest dostępny w sprzedaży.""", WATCH + ("975", "plotki", "rumor", "forum"))

# --- Task: Lavazza Qualità Oro beans, lowest price per kg (ignore delivery) -> hurtownia 84,83 zł/kg
COFFEE = ("kawa", "coffee", "lavazza", "ziarnista", "beans", "ziarno")
page("https://kawowy.example/lavazza-oro-1kg",
     "Lavazza Qualità Oro ziarnista 1 kg – Kawowy",
     """Kawa ziarnista Lavazza Qualità Oro, opakowanie 1 kg. Cena: 89,99 zł. Dostępna.""", COFFEE + ("oro", "1kg"))
page("https://delikatesy.example/lavazza-oro-500g",
     "Lavazza Qualità Oro ziarnista 500 g – Delikatesy",
     """Kawa ziarnista Lavazza Qualità Oro, 500 g. Cena: 42,99 zł. Dostępna.""", COFFEE + ("oro", "500g"))
page("https://hurtownia.example/lavazza-oro-6x1kg",
     "Lavazza Qualità Oro ziarnista 6 × 1 kg – Hurtownia",
     """Kawa ziarnista Lavazza Qualità Oro, karton 6 opakowań po 1 kg (razem 6 kg). Cena za karton: 509,00 zł.
Dostępna.""", COFFEE + ("oro", "6kg", "karton", "hurt", "bulk"))
page("https://promocja.example/lavazza-rossa-250g",
     "Lavazza Qualità Rossa ziarnista 250 g – Promocja",
     """Kawa ziarnista Lavazza Qualità ROSSA (nie Oro), 250 g. Cena: 19,99 zł. Dostępna.""", COFFEE + ("rossa", "250g"))

# --- Noise -------------------------------------------------------------------------------------
page("https://blog-tech.example/najlepsze-sluchawki-2026",
     "Najlepsze słuchawki 2026 – ranking Blog-Tech",
     """Ranking słuchawek z ANC: 1. Sony WH-1000XM6, 2. Bose QC Ultra 2, 3. Sennheiser Momentum 5.
Artykuł nie zawiera ofert sprzedaży.""", ("słuchawki", "headphones", "ranking", "sony", "bose"))
page("https://poradnik.example/jak-wybrac-wkretarke",
     "Jak wybrać wkrętarkę? Poradnik",
     """Poradnik: na co zwrócić uwagę przy wyborze wiertarko-wkrętarki: moment obrotowy, napięcie,
wersja solo czy zestaw z akumulatorami.""", ("wkrętarka", "drill", "poradnik", "guide", "bosch"))

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pages.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(P, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{len(P)} pages -> {out}")
