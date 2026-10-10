"""Source of the frozen web v2 (envs/shop/v2/{pl,en}/pages.json) and of its answer key (answers.json).

    python3 envs/shop/v2/build_pages.py

v2 is a separate snapshot per language, generated from the same data, so the English variant of a task is
not secretly a cross-lingual test. It contains every v1 page as noise plus the pages of six multi-hop research
tasks. Expected answers are COMPUTED from the same data the pages are written from. Everything is fictional
(reserved .example domains). Snapshot date: 2026-10-08. v2 must stay byte-identical once published.
"""

import itertools
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
V1 = os.path.join(os.path.dirname(HERE), "v1", "pages.json")
ANSWERS = {}
P = {"pl": [], "en": []}


def page(lang, url, title, text, keywords=()):
    P[lang].append({"url": url, "title": title, "keywords": list(keywords), "text": text.strip()})


def zl(x, lang):
    s = f"{x:,.2f}"
    return (s.replace(",", " ").replace(".", ",") + " zł") if lang == "pl" else ("PLN " + s)


# ------------------------------------------------------------------------------------------ 1. basket
ITEMS = {"router": ("Router Kwanta AX5400", "Kwanta AX5400 router"), "switch": ("Przełącznik Kwanta S8 (8 portów)", "Kwanta S8 switch (8 ports)"),
         "cable": ("Kabel sieciowy Cat6 20 m", "Cat6 network cable 20 m")}
NEED = {"router": 1, "switch": 1, "cable": 2}
SHOPS = {  # prices (None = not sold / out of stock), shipping rule, coupon rule
    "sieciowo.example": {"router": 579.00, "switch": 159.00, "cable": 39.00, "ship": ("flat_free_above", 14.99, 500.0), "coupon": None},
    "netmarket.example": {"router": 549.00, "switch": 149.00, "cable": 45.00, "ship": ("flat", 9.99, None),
                          "coupon": ("SIEC10", 0.10, 800.0, ("cable",))},   # 10% off orders >= 800 zł, not on cables
    "kablownia.example": {"router": None, "switch": 139.00, "cable": 29.00, "ship": ("flat_free_above", 19.99, 300.0), "coupon": None},
    "techhurt.example": {"router": 599.00, "switch": 135.00, "cable": None, "ship": ("free", 0.0, None), "coupon": None},
}


def order_cost(shop, items):
    s = SHOPS[shop]
    goods = sum(s[i] * NEED[i] for i in items)
    disc = 0.0
    if s["coupon"]:
        code, pct, minv, excl = s["coupon"]
        if goods >= minv:
            disc = pct * sum(s[i] * NEED[i] for i in items if i not in excl)
    kind, fee, thr = s["ship"]
    ship = 0.0 if kind == "free" else (0.0 if kind == "flat_free_above" and goods - disc >= thr else fee)
    return round(goods - disc + ship, 2)


def build_basket():
    best = None
    names = list(SHOPS)
    for assign in itertools.product(names, repeat=3):
        plan = dict(zip(["router", "switch", "cable"], assign))
        if any(SHOPS[plan[i]][i] is None for i in plan):
            continue
        by_shop = {}
        for i, sh in plan.items():
            by_shop.setdefault(sh, []).append(i)
        total = round(sum(order_cost(sh, its) for sh, its in by_shop.items()), 2)
        if best is None or total < best[0] - 1e-9:
            best = (total, by_shop)
    ANSWERS["basket"] = {"total": best[0], "shops": sorted(best[1]), "plan": best[1],
                         "plan_by_item": {i: sh for sh, its in best[1].items() for i in its}}
    for lang in ("pl", "en"):
        for shop, s in SHOPS.items():
            kind, fee, thr = s["ship"]
            if lang == "pl":
                ship = ("Dostawa gratis." if kind == "free" else
                        f"Dostawa kurierem {zl(fee, lang)}" + (f", darmowa od {zl(thr, lang)} (wartość zamówienia po rabatach)." if thr else " za zamówienie."))
                coup = (f" Kod {s['coupon'][0]}: −{int(s['coupon'][1] * 100)}% przy zamówieniu od {zl(s['coupon'][2], lang)}; rabat nie obejmuje kabli."
                        if s["coupon"] else "")
                page(lang, f"https://{shop}/dostawa", f"Dostawa i płatności – {shop}", ship + coup + " Jedna przesyłka na zamówienie.",
                     ("dostawa", "wysyłka", "koszt", "kod", "rabat", shop.split(".")[0]))
            else:
                ship = ("Free delivery." if kind == "free" else
                        f"Courier delivery {zl(fee, lang)}" + (f", free from {zl(thr, lang)} (order value after discounts)." if thr else " per order."))
                coup = (f" Code {s['coupon'][0]}: −{int(s['coupon'][1] * 100)}% on orders from {zl(s['coupon'][2], lang)}; the discount does not apply to cables."
                        if s["coupon"] else "")
                page(lang, f"https://{shop}/delivery", f"Delivery and payment – {shop}", ship + coup + " One shipment per order.",
                     ("delivery", "shipping", "cost", "code", "discount", shop.split(".")[0]))
            for key, (npl, nen) in ITEMS.items():
                price = s[key]
                name = npl if lang == "pl" else nen
                if price is None:
                    txt = ("Produkt niedostępny – brak w magazynie." if lang == "pl" else "Product unavailable – out of stock.")
                else:
                    txt = (f"{npl}. Cena: {zl(price, lang)} za sztukę. Dostępny." if lang == "pl" else f"{nen}. Price: {zl(price, lang)} each. In stock.")
                kw = ("kwanta", "router", "ax5400", "wifi") if key == "router" else ("kwanta", "przełącznik", "switch", "s8") if key == "switch" else \
                    ("kabel", "cable", "cat6", "utp", "20m", "sieciowy", "network")
                page(lang, f"https://{shop}/{key}", f"{name} – {shop}", txt, kw)


# ------------------------------------------------------------------------------------------ 2. multi-hop CTO
def build_cto():
    ANSWERS["cto"] = {"company": "Sensilo", "cto": "Joanna Kruk", "outdated_cto": "Marek Lis", "future_cto": "Paweł Zych"}
    for lang in ("pl", "en"):
        pl = lang == "pl"
        page(lang, "https://zloty-bit.example/2026", "Złoty Bit 2026 – laureaci" if pl else "Golden Bit 2026 – winners",
             ("Laureaci konkursu Złoty Bit 2026: kategoria FinTech – Liczydło S.A.; kategoria IoT – Sensilo Sp. z o.o.; "
              "kategoria AI – Myślnik Sp. z o.o. Gala odbyła się 18 września 2026 r.") if pl else
             ("Golden Bit 2026 winners: FinTech – Liczydło S.A.; IoT – Sensilo Sp. z o.o.; AI – Myślnik Sp. z o.o. "
              "The gala took place on 18 September 2026."), ("złoty", "bit", "golden", "award", "nagroda", "laureaci", "winners", "2026", "iot"))
        page(lang, "https://zloty-bit.example/2025", "Złoty Bit 2025 – laureaci" if pl else "Golden Bit 2025 – winners",
             ("Laureaci 2025: FinTech – Kasa24; IoT – Termix S.A.; AI – Liczydło S.A.") if pl else
             ("Winners 2025: FinTech – Kasa24; IoT – Termix S.A.; AI – Liczydło S.A."), ("złoty", "bit", "golden", "award", "laureaci", "winners", "2025", "iot"))
        page(lang, "https://sensilo.example/zespol" if pl else "https://sensilo.example/team", "Sensilo – zespół" if pl else "Sensilo – team",
             ("Zarząd Sensilo (stan na listopad 2025): CEO – Ewa Bąk, CTO – Marek Lis, CFO – Adam Sowa.") if pl else
             ("Sensilo management (as of November 2025): CEO – Ewa Bąk, CTO – Marek Lis, CFO – Adam Sowa."),
             ("sensilo", "zarząd", "team", "management", "cto", "ceo"))
        page(lang, "https://sensilo.example/aktualnosci/nowy-cto" if pl else "https://sensilo.example/news/new-cto",
             "Sensilo: zmiana na stanowisku CTO" if pl else "Sensilo: new CTO",
             ("1 września 2026 r. funkcję CTO objęła Joanna Kruk. Dotychczasowy CTO Marek Lis przeszedł do rady nadzorczej.") if pl else
             ("On 1 September 2026 Joanna Kruk became CTO. The previous CTO, Marek Lis, moved to the supervisory board."),
             ("sensilo", "cto", "aktualności", "news", "zmiana", "new"))
        page(lang, "https://sensilo.example/aktualnosci/sukcesja" if pl else "https://sensilo.example/news/succession",
             "Sensilo: plan sukcesji w zarządzie" if pl else "Sensilo: board succession plan",
             ("5 października 2026 r. Joanna Kruk, CTO Sensilo, ogłosiła, że z końcem roku odchodzi z firmy. Do 31 grudnia 2026 r. "
              "pełni funkcję CTO; od 1 stycznia 2027 r. funkcję CTO obejmie Paweł Zych, obecnie szef działu R&D.") if pl else
             ("On 5 October 2026 Joanna Kruk, Sensilo's CTO, announced she will leave the company at the end of the year. She "
              "remains CTO until 31 December 2026; from 1 January 2027 the CTO role passes to Paweł Zych, currently head of R&D."),
             ("sensilo", "cto", "aktualności", "news", "sukcesja", "succession", "zarząd", "board"))
        page(lang, "https://profile-zawodowe.example/pawel-zych", "Paweł Zych – CTO, Sensilo" if pl else "Paweł Zych – CTO, Sensilo",
             ("Paweł Zych. CTO w Sensilo (od 2027). Wcześniej szef działu R&D w Sensilo.") if pl else
             ("Paweł Zych. CTO at Sensilo (from 2027). Previously head of R&D at Sensilo."),
             ("sensilo", "cto", "profil", "profile", "zych"))
        page(lang, "https://termix.example/o-nas" if pl else "https://termix.example/about", "Termix – o nas" if pl else "Termix – about us",
             ("CTO Termix: Piotr Wilk.") if pl else ("Termix CTO: Piotr Wilk."), ("termix", "cto", "o nas", "about"))


# ------------------------------------------------------------------------------------------ 3. laptop spec filter
LAPTOPS = [  # id, shop, model, ram_installed, ram_max, weight, screen, price, delivery, stock, warranty_months
    ("l1", "laptopy.example", "Kwanta Book 14 Pro", 32, 32, 1.42, 14.0, 5899.0, 0.0, True, 24),
    ("l2", "laptopy.example", "Kwanta Book 14", 16, 32, 1.38, 14.0, 4299.0, 0.0, True, 24),       # RAM upgradeable only
    ("l3", "komputerowo.example", "Orbis Air 14", 32, 64, 1.52, 14.0, 5499.0, 0.0, True, 24),       # too heavy
    ("l4", "komputerowo.example", "Orbis Slim 14", 32, 32, 1.35, 14.0, 5999.0, 29.0, True, 24),     # 6028 with delivery
    ("l5", "elektromarket.example", "Lumen X14", 32, 32, 1.29, 14.0, 5649.0, 0.0, False, 24),       # out of stock
    ("l6", "elektromarket.example", "Lumen X13", 32, 32, 1.10, 13.3, 4999.0, 0.0, True, 24),        # screen too small
    ("l7", "tanie-laptopy.example", "Vento 14 Ultra", 32, 32, 1.45, 14.0, 5599.0, 19.0, True, 12),  # warranty 12
    ("l8", "tanie-laptopy.example", "Vento 14 Plus", 32, 64, 1.48, 14.0, 5749.0, 19.0, True, 24),   # valid: 5768
    ("l9", "laptopy.example", "Kwanta Book 16", 32, 64, 1.89, 16.0, 5299.0, 0.0, True, 24),          # too heavy
    ("l10", "komputerowo.example", "Orbis Pro 14", 32, 32, 1.47, 14.0, 5799.0, 0.0, True, 36),      # valid: 5799
]


def build_laptops():
    ok = [l for l in LAPTOPS if l[3] >= 32 and l[5] <= 1.5 and l[6] >= 14.0 and l[7] + l[8] <= 6000 and l[9] and l[10] >= 24]
    best = min(ok, key=lambda l: l[7] + l[8])
    ANSWERS["laptop"] = {"url": f"https://{best[1]}/laptop/{best[0]}", "price": round(best[7] + best[8], 2),
                         "valid": [l[0] for l in ok]}
    for lang in ("pl", "en"):
        pl = lang == "pl"
        for lid, shop, model, ram, rmax, w, scr, price, dl, stock, war in LAPTOPS:
            if pl:
                t = (f"{model}. Pamięć RAM: {ram} GB" + (f" (rozszerzalna do {rmax} GB)" if rmax > ram else "") +
                     f". Waga: {str(w).replace('.', ',')} kg. Ekran: {str(scr).replace('.', ',')}\". Cena: {zl(price, lang)}. "
                     f"Dostawa: {'gratis' if dl == 0 else zl(dl, lang)}. {'Dostępny.' if stock else 'Chwilowo niedostępny.'} Gwarancja: {war} mies.")
            else:
                t = (f"{model}. RAM: {ram} GB" + (f" (upgradeable to {rmax} GB)" if rmax > ram else "") +
                     f". Weight: {w} kg. Screen: {scr}\". Price: {zl(price, lang)}. "
                     f"Delivery: {'free' if dl == 0 else zl(dl, lang)}. {'In stock.' if stock else 'Temporarily unavailable.'} Warranty: {war} months.")
            page(lang, f"https://{shop}/laptop/{lid}", f"{model} – {shop}", t,
                 ("laptop", "notebook", "32", "gb", "ram", "14", "lekki", "light", model.split()[0].lower()))


# ------------------------------------------------------------------------------------------ 4. event + train
TRAINS = [  # id, kind, departs, arrives (2026-10-22, Warszawa Centralna -> Poznań Główny)
    ("TLK 61107", "TLK", "05:02", "09:05"), ("IC 3510", "IC", "05:10", "08:20"), ("EIP 3500", "EIP", "05:55", "08:25"),
    ("EIP 3502", "EIP", "06:15", "08:45"), ("IC 3512", "IC", "06:40", "09:50")]
FARES = {"TLK": 59.0, "IC": 79.0, "EIP": 169.0}


def build_event():
    start = "09:30"
    latest = "08:30"  # at least 60 minutes before the start
    cancelled = {"IC 3510"}  # notice page
    ok = [t for t in TRAINS if t[3] <= latest and t[0] not in cancelled]
    price = lambda t: round(FARES[t[1]] * (0.7 if t[1] == "EIP" else 1.0), 2)  # 30% early booking (>= 7 days) on EIP only
    best = min(ok, key=price)
    ANSWERS["event"] = {"date": "2026-10-22", "city": "Poznań", "train": best[0], "price": price(best)}
    for lang in ("pl", "en"):
        pl = lang == "pl"
        page(lang, "https://kongres-automatyzacji.example/2026", "Kongres Automatyzacji 2026" if pl else "Automation Congress 2026",
             ("Kongres Automatyzacji 2026 odbędzie się 22 października 2026 r. w Poznaniu (MTP, pawilon 15). Początek obrad: 9:30.") if pl else
             ("The Automation Congress 2026 takes place on 22 October 2026 in Poznań (MTP, hall 15). Opening session: 9:30."),
             ("kongres", "congress", "automatyzacji", "automation", "2026", "poznań"))
        page(lang, "https://kongres-automatyzacji.example/2025", "Kongres Automatyzacji 2025" if pl else "Automation Congress 2025",
             ("Edycja 2025 odbyła się 16 października 2025 r. we Wrocławiu (Hala Stulecia). Początek 10:00.") if pl else
             ("The 2025 edition took place on 16 October 2025 in Wrocław (Centennial Hall). Start 10:00."),
             ("kongres", "congress", "automatyzacji", "automation", "2025", "wrocław"))
        rows = "\n".join(f"{t[0]}: {t[2]} → {t[3]}" for t in TRAINS)
        page(lang, "https://rozklad-pkp.example/warszawa-poznan/2026-10-22", "Rozkład: Warszawa Centralna → Poznań Główny, 22.10.2026" if pl else
             "Timetable: Warszawa Centralna → Poznań Główny, 22 Oct 2026", rows,
             ("rozkład", "timetable", "pociąg", "train", "warszawa", "poznań", "22.10.2026", "2026-10-22"))
        page(lang, "https://rozklad-pkp.example/komunikaty", "Komunikaty przewoźnika" if pl else "Carrier notices",
             ("22.10.2026 pociąg IC 3510 Warszawa Centralna – Poznań Główny nie kursuje z powodu prac torowych. Zastępczej komunikacji nie przewidziano.") if pl else
             ("On 22 Oct 2026 train IC 3510 Warszawa Centralna – Poznań Główny does not run due to track works. No replacement service."),
             ("komunikaty", "notices", "odwołany", "cancelled", "ic", "3510", "prace", "torowe"))
        page(lang, "https://rozklad-pkp.example/cennik", "Cennik biletów – trasa Warszawa–Poznań" if pl else "Fares – Warszawa–Poznań",
             (f"Bilet normalny, klasa 2: TLK {zl(59, lang)}, IC {zl(79, lang)}, EIP {zl(169, lang)}. "
              "Przedsprzedaż: przy zakupie co najmniej 7 dni przed podróżą bilety EIP tańsze o 30% (dotyczy wyłącznie EIP).") if pl else
             (f"Standard ticket, 2nd class: TLK {zl(59, lang)}, IC {zl(79, lang)}, EIP {zl(169, lang)}. "
              "Early booking: buying at least 7 days before travel makes EIP tickets 30% cheaper (EIP only)."),
             ("cennik", "fares", "bilet", "ticket", "cena", "price", "eip", "ic", "tlk"))


# ------------------------------------------------------------------------------------------ 5. current regulation value
def build_relief():
    ANSWERS["relief"] = {"limit": 4500, "since": "2026-07-01"}
    for lang in ("pl", "en"):
        pl = lang == "pl"
        page(lang, "https://ministerstwo-finansow.example/komunikat-ulga-start", "Komunikat: ulga Start dla mikrofirm – nowy limit" if pl else
             "Notice: Start relief for micro-businesses – new limit",
             ("Od 1 lipca 2026 r. maksymalna kwota ulgi Start wynosi 4 500 zł rocznie (wcześniej 3 000 zł). Komunikat z 2 czerwca 2026 r.") if pl else
             ("From 1 July 2026 the maximum Start relief is PLN 4,500 per year (previously PLN 3,000). Notice of 2 June 2026."),
             ("ulga", "relief", "start", "mikrofirm", "micro", "limit", "kwota", "2026"))
        page(lang, "https://portal-podatkowy.example/ulga-start-2024", "Ulga Start – poradnik 2024" if pl else "Start relief – 2024 guide",
             ("Maksymalna kwota ulgi Start to 3 000 zł rocznie.") if pl else ("The maximum Start relief is PLN 3,000 per year."),
             ("ulga", "relief", "start", "poradnik", "guide", "limit", "kwota", "2024"))
        page(lang, "https://forum-firmowe.example/ulga-start", "Forum: ile wynosi ulga Start?" if pl else "Forum: how much is the Start relief?",
             ("Użytkownik Kowal99: od tego roku ulga Start to 5 000 zł, sprawdzałem!") if pl else ("User Kowal99: from this year the Start relief is PLN 5,000, I checked!"),
             ("ulga", "relief", "start", "forum", "ile", "how much"))
        page(lang, "https://blog-ksiegowy.example/2025/ulga-start-zmiany", "Ulga Start: planowane zmiany (2025)" if pl else "Start relief: planned changes (2025)",
             ("Projekt zakłada podniesienie ulgi Start do 3 500 zł od 2026 r. Projekt jest na etapie konsultacji.") if pl else
             ("The draft would raise the Start relief to PLN 3,500 from 2026. The draft is under consultation."),
             ("ulga", "relief", "start", "zmiany", "changes", "projekt", "draft", "2025"))


# ------------------------------------------------------------------------------------------ 6. count offers
# shop, model, condition, price, delivery rule, availability, note
#   delivery rule: number (flat) or (fee, free_from); availability: stock | out | preorder
KETTLES = [
    ("agd-swiat.example", "K2", "new", 179.0, 0.0, "stock", None),
    ("czajniki.example", "K2", "new", 189.0, 15.0, "stock", None),                  # 204.00
    ("kuchnia24.example", "K2", "new", 159.0, 19.99, "stock", None),                # 178.99
    ("outlet-agd.example", "K2", "used", 99.0, 0.0, "stock", None),                 # used
    ("domowe.example", "K2", "new", 195.0, 0.0, "out", None),                       # out of stock
    ("elektromarket.example", "K2", "new", 198.99, 0.0, "stock", None),             # 198.99
    ("megasklep.example", "K2", "new", 185.0, 14.0, "stock", None),                 # 199.00, not below
    ("tanioagd.example", "K2", "new", 149.0, 49.0, "stock", None),                  # 198.00
    ("sklep-kwanta.example", "K2", "new", 169.0, 9.99, "stock", None),              # 178.99
    ("sklep-agd24.example", "K2", "new", 185.0, (14.99, 199.0), "stock", None),     # free only from 199 -> 199.99
    ("agdmax.example", "K2", "new", 189.0, (12.99, 150.0), "stock", None),          # free from 150 -> 189.00
    ("elektro-dom.example", "K2 Pro", "new", 149.0, 0.0, "stock", None),            # a different model
    ("dom-i-kuchnia.example", "K2", "new", 179.0, 0.0, "preorder", None),           # pre-order
    ("kuchenne.example", "K2", "new", 199.0, 0.0, "stock", "promo_expired"),        # promo price 179 ended 30 Sep
]


def kettle_total(price, rule):
    if isinstance(rule, tuple):
        fee, free_from = rule
        return round(price + (0.0 if price >= free_from else fee), 2)
    return round(price + rule, 2)


def build_count():
    ok = [k for k in KETTLES if k[1] == "K2" and k[2] == "new" and k[5] == "stock" and kettle_total(k[3], k[4]) < 199.0]
    ANSWERS["count"] = {"count": len(ok), "shops": [k[0] for k in ok]}
    for lang in ("pl", "en"):
        pl = lang == "pl"
        for shop, model, cond, price, rule, avail, note in KETTLES:
            name = f"Czajnik Kwanta {model}" if pl else f"Kwanta {model} kettle"
            if isinstance(rule, tuple):
                dl = (f"Dostawa {zl(rule[0], lang)}, gratis od {zl(rule[1], lang)}." if pl else
                      f"Delivery {zl(rule[0], lang)}, free from {zl(rule[1], lang)}.")
            else:
                dl = (f"Dostawa: {'gratis' if rule == 0 else zl(rule, lang)}." if pl else
                      f"Delivery: {'free' if rule == 0 else zl(rule, lang)}.")
            av = {"stock": ("Dostępny.", "In stock."), "out": ("Brak w magazynie.", "Out of stock."),
                  "preorder": ("Przedsprzedaż: wysyłka od 16 listopada 2026 r.", "Pre-order: ships from 16 November 2026.")}[avail][0 if pl else 1]
            pr = (f"Cena: {zl(price, lang)}." if pl else f"Price: {zl(price, lang)}.")
            if note == "promo_expired":
                pr += (f" Promocja: {zl(179.0, lang)} do 30 września 2026 r." if pl else f" Promotion: {zl(179.0, lang)} until 30 September 2026.")
            t = (f"{name}, 1,7 l. Stan: {'nowy' if cond == 'new' else 'używany'}. {pr} {dl} {av}" if pl else
                 f"{name}, 1.7 l. Condition: {cond}. {pr} {dl} {av}")
            slug = "czajnik-kwanta-" + model.lower().replace(" ", "-")
            page(lang, f"https://{shop}/{slug}", f"{name} – {shop}", t,
                 ("czajnik", "kettle", "kwanta", "k2", "elektryczny", "electric") + (("pro",) if "Pro" in model else ()))


if __name__ == "__main__":
    for fn in (build_basket, build_cto, build_laptops, build_event, build_relief, build_count):
        fn()
    v1 = json.load(open(V1, encoding="utf-8"))
    for lang in ("pl", "en"):
        os.makedirs(os.path.join(HERE, lang), exist_ok=True)
        pages = v1 + P[lang]  # v1 pages (Polish shop pages) stay as realistic noise in both snapshots
        urls = [p["url"] for p in pages]
        assert len(urls) == len(set(urls)), "duplicate URL"
        with open(os.path.join(HERE, lang, "pages.json"), "w", encoding="utf-8") as f:
            json.dump(pages, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(lang, len(pages), "pages")
    with open(os.path.join(HERE, "answers.json"), "w", encoding="utf-8") as f:
        json.dump(ANSWERS, f, ensure_ascii=False, indent=1)
        f.write("\n")
    for k, v in ANSWERS.items():
        print(k, v)
