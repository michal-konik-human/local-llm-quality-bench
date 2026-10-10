"""Source of the long documents in envs/docs/v1/ and of their answer key (answers.json).

    python3 envs/docs/v1/build_docs.py

Every document is generated from fixed data with a fixed seed, and the expected answers are COMPUTED
from the same data, so the answer key cannot drift from the documents. All content is fictional.
v1 must stay byte-identical once published; new documents go into v2.
"""

import csv
import io
import json
import math
import os
import random

OUT = os.path.dirname(os.path.abspath(__file__))
ANSWERS = {}


def write(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


# ======================================================================================== 1. incident log
def build_log():
    rng = random.Random(1101)
    hosts = [f"web-0{i}" for i in range(1, 7)] + ["api-01", "api-02", "api-03", "db-01", "db-02", "cache-01"]
    noise = [
        ("INFO", "nginx", "GET /api/v2/orders 200 {ms}ms"), ("INFO", "nginx", "GET /health 200 {ms}ms"),
        ("INFO", "app", "job sync-catalog finished in {ms}ms"), ("INFO", "app", "cache hit ratio {pct}%"),
        ("WARN", "app", "slow query {ms}ms (threshold 500ms)"), ("INFO", "kernel", "eth0 link up 25000Mb/s"),
        ("INFO", "postgres", "checkpoint complete: wrote {n} buffers"), ("WARN", "nginx", "upstream timed out (110) after {ms}ms"),
        ("INFO", "app", "user session created id={hex}"), ("INFO", "cron", "logrotate finished"),
    ]
    events = []
    t0 = 13 * 3600
    for _ in range(560):
        sec = t0 + rng.randrange(0, 3 * 3600)
        lvl, comp, msg = rng.choice(noise)
        events.append([sec, rng.choice(hosts), lvl, comp, msg.format(ms=rng.randrange(3, 1900), pct=rng.randrange(71, 99),
                                                                       n=rng.randrange(200, 9000), hex=f"{rng.getrandbits(32):08x}")])

    def at(h, m, s):
        return 13 * 3600 + (h - 13) * 3600 + m * 60 + s
    story = [
        (at(13, 58, 2), "db-02", "WARN", "smartd", "W-DISK-01 SMART pre-failure attribute 5 (Reallocated_Sector_Ct) on /dev/nvme1n1"),
        (at(14, 1, 37), "web-04", "WARN", "app", "E-DISK-03 temporary write error on /var/cache, retry 1/3 succeeded"),
        (at(14, 5, 40), "cache-01", "ERROR", "redis", "E-DISK-02 fsync failed on appendonly.aof: Input/output error"),
        (at(14, 7, 13), "db-02", "ERROR", "postgres", "E-DISK-07 could not write to file pg_wal/000000010000004A: No space left on device"),
        (at(14, 31, 5), "db-02", "INFO", "systemd", "restarted postgresql.service"),
        (at(14, 44, 18), "api-02", "INFO", "systemd", "restarted api-gateway.service"),
        (at(14, 52, 9), "api-02", "INFO", "systemd", "restarted pgbouncer.service"),
        (at(15, 20, 0), "web-02", "INFO", "systemd", "restarted node-exporter.service"),
    ]
    for e in story:
        events.append(list(e))
    # db-02 errors 14:07..14:51, api errors until the pgbouncer restart; nothing at ERROR level afterwards
    for _ in range(41):
        events.append([rng.randrange(at(14, 7, 20), at(14, 51, 50)), "db-02", "ERROR", "postgres",
                       rng.choice(["E-DB-11 connection slot reserved: too many clients",
                                   "E-DISK-07 could not extend file base/16384/2619: No space left on device",
                                   "E-DB-04 canceling statement due to lock timeout"])])
    for _ in range(9):  # db-02 errors outside the 14:00-14:59 window must not be counted
        events.append([rng.randrange(at(13, 10, 0), at(13, 50, 0)), "db-02", "WARN", "postgres", "E-DB-04 lock wait 2.1s (warning only)"])
    for _ in range(23):
        events.append([rng.randrange(at(14, 8, 0), at(14, 52, 0)), rng.choice(["api-01", "api-02", "api-03"]), "ERROR",
                       "api", "E-API-21 upstream db pool exhausted (pgbouncer)"])
    events.sort(key=lambda e: (e[0], e[1]))
    lines = []
    for sec, host, lvl, comp, msg in events:
        hh, mm, ss = sec // 3600, sec % 3600 // 60, sec % 60
        lines.append(f"2026-10-07T{hh:02d}:{mm:02d}:{ss:02d}Z {host:9s} {lvl:5s} {comp}: {msg}")
    write("incident-log.txt", "\n".join(lines))
    # answer key, computed from the lines themselves
    first_disk = next(l.split()[1] for l in lines if " ERROR " in l and "E-DISK" in l)
    n_db02 = sum(1 for l in lines if l.split()[1] == "db-02" and l.split()[2] == "ERROR" and "T14:" in l.split()[0])
    last_err = max(l.split()[0] for l in lines if l.split()[2] == "ERROR")
    restart = min((l for l in lines if "restarted" in l and l.split()[0] > last_err), key=lambda l: l.split()[0])
    service = restart.split("restarted ")[1].replace(".service", "")
    ANSWERS["incident-log"] = {"first_disk_error_host": first_disk, "db02_errors_14h": n_db02, "fix_service": service,
                               "lines": len(lines)}


# ======================================================================================== 2. SLA contract
CONTRACT_FILLER = {
    "pl": [
        ("Postanowienia ogólne", ["Umowa określa zasady świadczenia przez Dostawcę usług utrzymania i rozwoju systemu {sys} na rzecz Zamawiającego.",
                                  "Integralną część Umowy stanowią załączniki wymienione w §14. W razie sprzeczności pierwszeństwo ma treść Umowy, chyba że podpisany aneks stanowi inaczej.",
                                  "Strony zobowiązują się współdziałać w dobrej wierze w celu należytego wykonania Umowy."]),
        ("Definicje", ["Dzień Roboczy oznacza każdy dzień od poniedziałku do piątku z wyłączeniem dni ustawowo wolnych od pracy w Polsce.",
                       "Zgłoszenie oznacza informację o Incydencie przekazaną przez Zamawiającego za pośrednictwem portalu serwisowego lub telefonicznie.",
                       "Incydent oznacza każde zdarzenie powodujące przerwę lub obniżenie jakości działania Systemu.",
                       "Okno Serwisowe oznacza uzgodniony z wyprzedzeniem okres, w którym Dostawca wykonuje prace planowe.",
                       "Niedostępność oznacza okres, w którym System nie realizuje funkcji podstawowych wymienionych w Załączniku nr 1.",
                       "Opłata Miesięczna oznacza wynagrodzenie, o którym mowa w §5 ust. 2."]),
        ("Przedmiot Umowy", ["Dostawca zapewnia utrzymanie infrastruktury, monitorowanie, obsługę Zgłoszeń oraz aktualizacje bezpieczeństwa.",
                             "Rozwój Systemu realizowany jest na podstawie odrębnych zamówień, wycenianych zgodnie z Załącznikiem nr 3.",
                             "Dostawca prowadzi dokumentację techniczną i udostępnia ją Zamawiającemu na każde żądanie."]),
        ("Obowiązki Stron", ["Zamawiający zapewni Dostawcy dostęp do środowisk testowych w zakresie niezbędnym do wykonania Umowy.",
                             "Dostawca wyznaczy kierownika usługi odpowiedzialnego za kontakt z Zamawiającym.",
                             "Strony będą odbywać comiesięczne przeglądy jakości usług, z których sporządza się notatkę."]),
    ],
    "en": [
        ("General provisions", ["This Agreement sets out the terms on which the Provider maintains and develops the {sys} system for the Customer.",
                                "The annexes listed in §14 form an integral part of the Agreement. In case of conflict the Agreement prevails unless a signed amendment provides otherwise.",
                                "The Parties shall cooperate in good faith to perform the Agreement properly."]),
        ("Definitions", ["Business Day means any day from Monday to Friday excluding public holidays in Poland.",
                         "Ticket means information about an Incident submitted by the Customer through the service portal or by phone.",
                         "Incident means any event causing an interruption or degradation of the System.",
                         "Maintenance Window means a period agreed in advance in which the Provider performs planned work.",
                         "Unavailability means a period in which the System does not perform the core functions listed in Annex 1.",
                         "Monthly Fee means the remuneration referred to in §5(2)."]),
        ("Subject matter", ["The Provider maintains the infrastructure, monitors it, handles Tickets and applies security updates.",
                            "Development of the System is ordered separately and priced according to Annex 3.",
                            "The Provider keeps technical documentation and makes it available to the Customer on request."]),
        ("Obligations of the Parties", ["The Customer shall give the Provider access to test environments to the extent necessary.",
                                        "The Provider shall appoint a service manager responsible for contact with the Customer.",
                                        "The Parties shall hold monthly service quality reviews and record them in a note."]),
    ],
}
CONTRACT_TAIL = {
    "pl": [("Poufność", ["Strony zachowają w tajemnicy wszelkie informacje uzyskane w związku z Umową, z wyjątkiem informacji publicznie dostępnych.",
                          "Obowiązek poufności obowiązuje przez okres pięciu lat od rozwiązania Umowy."]),
           ("Ochrona danych", ["Powierzenie przetwarzania danych osobowych reguluje odrębna umowa stanowiąca Załącznik nr 4.",
                               "Dostawca stosuje środki techniczne i organizacyjne odpowiednie do ryzyka."]),
           ("Odpowiedzialność", ["Łączna odpowiedzialność Dostawcy z tytułu Umowy, poza karami umownymi, jest ograniczona do wysokości wynagrodzenia za ostatnie dwanaście miesięcy.",
                                 "Ograniczenie nie dotyczy szkody wyrządzonej umyślnie."]),
           ("Rozwiązanie Umowy", ["Każda ze Stron może wypowiedzieć Umowę z zachowaniem trzymiesięcznego okresu wypowiedzenia ze skutkiem na koniec miesiąca kalendarzowego.",
                                  "Zamawiający może rozwiązać Umowę bez wypowiedzenia, jeżeli Dostępność w dwóch kolejnych miesiącach spadnie poniżej 98%."]),
           ("Postanowienia końcowe", ["Zmiany Umowy wymagają formy pisemnej pod rygorem nieważności i obowiązują od daty wskazanej w aneksie.",
                                      "Spory rozstrzyga sąd właściwy dla siedziby Zamawiającego.",
                                      "Załączniki: nr 1 Funkcje podstawowe, nr 2 Aneks nr 1, nr 3 Cennik prac rozwojowych, nr 4 Umowa powierzenia, nr 5 Protokoły z przeglądów jakości."])],
    "en": [("Confidentiality", ["The Parties shall keep confidential all information obtained in connection with the Agreement, except publicly available information.",
                                "The confidentiality obligation lasts for five years after the Agreement ends."]),
           ("Data protection", ["Processing of personal data is governed by a separate agreement forming Annex 4.",
                                "The Provider applies technical and organisational measures appropriate to the risk."]),
           ("Liability", ["The Provider's total liability under the Agreement, other than contractual penalties, is limited to the fees for the last twelve months.",
                          "The limitation does not apply to damage caused wilfully."]),
           ("Termination", ["Either Party may terminate the Agreement with three months' notice effective at the end of a calendar month.",
                            "The Customer may terminate without notice if Availability falls below 98% in two consecutive months."]),
           ("Final provisions", ["Amendments require written form to be valid and apply from the date stated in the amendment.",
                                 "Disputes are resolved by the court competent for the Customer's registered office.",
                                 "Annexes: 1 Core functions, 2 Amendment No. 1, 3 Price list for development work, 4 Data processing agreement, 5 Service quality review minutes."])],
}


def build_contract():
    fee, target, rate_old, rate_new, cap_pct, credit = 48000, 99.5, 2.0, 3.0, 20, 800
    systems = {"pl": "obsługi zamówień „Kwadrat Order”", "en": "\"Kwadrat Order\" order management"}
    sla = {
        "pl": [f"Opłata Miesięczna wynosi {fee:,} zł netto i jest płatna z dołu w terminie 21 dni od doręczenia faktury.".replace(",", " "),
               f"Dostawca zapewnia Dostępność Systemu na poziomie co najmniej {str(target).replace('.', ',')}% w każdym miesiącu kalendarzowym.",
               "Czas Niedostępności liczy się w pełnych minutach, od chwili rozpoczęcia Niedostępności do chwili przywrócenia funkcji podstawowych.",
               "Dopuszczalny czas Niedostępności w danym miesiącu (Limit) oblicza się jako 0,5% liczby minut w tym miesiącu.",
               f"Za każdą rozpoczętą godzinę Niedostępności przekraczającej Limit Dostawca zapłaci karę umowną w wysokości {str(rate_old).replace('.0', '')}% Opłaty Miesięcznej.",
               "Do czasu Niedostępności nie wlicza się: (a) prac w Oknie Serwisowym zapowiedzianym Zamawiającemu co najmniej 72 godziny wcześniej, o ile pojedyncze okno nie przekracza 4 godzin; (b) przerw spowodowanych siłą wyższą, potwierdzoną pisemnie przez obie Strony.",
               f"Łączna wysokość kar umownych za dany miesiąc nie może przekroczyć {cap_pct}% Opłaty Miesięcznej.",
               "Kary umowne za dany miesiąc pomniejsza się o kwotę rekompensat (kredytów serwisowych) przyznanych przez Dostawcę za ten sam miesiąc."],
        "en": [f"The Monthly Fee is PLN {fee:,} net, payable in arrears within 21 days of receipt of the invoice.",
               f"The Provider shall ensure System Availability of at least {target}% in each calendar month.",
               "Unavailability is counted in full minutes, from the moment it starts until the core functions are restored.",
               "The permitted Unavailability in a month (the Allowance) is 0.5% of the number of minutes in that month.",
               f"For each started hour of Unavailability exceeding the Allowance the Provider shall pay a contractual penalty of {str(rate_old).replace('.0', '')}% of the Monthly Fee.",
               "Unavailability does not include: (a) work in a Maintenance Window announced to the Customer at least 72 hours in advance, provided a single window does not exceed 4 hours; (b) interruptions caused by force majeure confirmed in writing by both Parties.",
               f"Total contractual penalties for a month may not exceed {cap_pct}% of the Monthly Fee.",
               "Contractual penalties for a month are reduced by any compensation (service credits) granted by the Provider for the same month."],
    }
    annexes = {
        "pl": [("Załącznik nr 2 – Aneks nr 1 (podpisany 14 sierpnia 2026 r.)",
                [f"§6 ust. 4 otrzymuje brzmienie: „Za każdą rozpoczętą godzinę Niedostępności przekraczającej Limit Dostawca zapłaci karę umowną w wysokości {str(rate_new).replace('.0', '')}% Opłaty Miesięcznej.”",
                 "Aneks obowiązuje od 1 września 2026 r. Pozostałe postanowienia Umowy pozostają bez zmian."]),
               ("Projekt Aneksu nr 2 (NIEPODPISANY – do dalszych negocjacji)",
                ["Proponuje się podniesienie kary za rozpoczętą godzinę ponad Limit do 5% Opłaty Miesięcznej oraz zniesienie limitu z §6 ust. 6.",
                 "Projekt nie został przyjęty przez Dostawcę."]),
               ("Załącznik nr 5 – Protokół z przeglądu jakości za wrzesień 2026 r.",
                [f"Dostawca przyznał Zamawiającemu jednorazowy kredyt serwisowy w kwocie {credit} zł netto za wrzesień 2026 r. w związku z incydentem z 3 września.",
                 "Strony potwierdzają, że przerwa z 24 września 2026 r. (awaria regionalnej sieci energetycznej) stanowi siłę wyższą w rozumieniu §6 ust. 5 lit. b."])],
        "en": [("Annex 2 – Amendment No. 1 (signed on 14 August 2026)",
                [f"§6(4) is replaced by: \"For each started hour of Unavailability exceeding the Allowance the Provider shall pay a contractual penalty of {str(rate_new).replace('.0', '')}% of the Monthly Fee.\"",
                 "The amendment applies from 1 September 2026. All other provisions remain unchanged."]),
               ("Draft Amendment No. 2 (NOT SIGNED – subject to further negotiation)",
                ["It is proposed to raise the penalty per started hour above the Allowance to 5% of the Monthly Fee and to remove the cap in §6(6).",
                 "The draft was not accepted by the Provider."]),
               ("Annex 5 – Minutes of the service quality review for September 2026",
                [f"The Provider granted the Customer a one-off service credit of PLN {credit} net for September 2026 in connection with the incident of 3 September.",
                 "The Parties confirm that the interruption of 24 September 2026 (regional power grid failure) constitutes force majeure within the meaning of §6(5)(b)."])],
    }
    outages = [  # (day, minutes, kind, notice_h)
        (3, 95, "unplanned", None), (10, 180, "planned", 96), (17, 300, "planned", 48), (24, 50, "force_majeure", None)]
    # ---- answer, computed
    minutes_month = 30 * 24 * 60
    allowance = 0.005 * minutes_month
    counted = sum(m for d, m, k, n in outages if k == "unplanned" or (k == "planned" and not (n >= 72 and m <= 240)))
    excess = max(0, counted - allowance)
    hours = math.ceil(excess / 60)
    penalty = min(hours * rate_new / 100 * fee, cap_pct / 100 * fee)
    ANSWERS["contract"] = {"allowance_min": allowance, "counted_min": counted, "started_hours": hours,
                           "penalty_before_credit": penalty, "answer_pln": round(penalty - credit, 2)}
    for lang in ("pl", "en"):
        rng = random.Random(1202)
        L = [("UMOWA O ŚWIADCZENIE USŁUG UTRZYMANIA SYSTEMU nr 07/2026" if lang == "pl" else "SYSTEM MAINTENANCE SERVICES AGREEMENT No. 07/2026"),
             "", ("zawarta 2 stycznia 2026 r. pomiędzy Kwadrat Sp. z o.o. (Zamawiający) a Chmurex S.A. (Dostawca)." if lang == "pl" else
                  "concluded on 2 January 2026 between Kwadrat Sp. z o.o. (the Customer) and Chmurex S.A. (the Provider)."), ""]
        sec = 0

        def section(title, paras):
            nonlocal sec
            sec += 1
            L.append(f"§{sec}. {title}")
            for i, p in enumerate(paras, 1):
                L.append(f"{i}. {p.format(sys=systems[lang])}")
            L.append("")
        filler = CONTRACT_FILLER[lang]
        for title, paras in filler:
            section(title, paras)
        section("Wynagrodzenie" if lang == "pl" else "Remuneration",
                ["Wynagrodzenie za prace rozwojowe ustala się na podstawie stawek z Załącznika nr 3." if lang == "pl" else
                 "Fees for development work are based on the rates in Annex 3.", sla[lang][0]])
        section("Poziom usług i kary umowne" if lang == "pl" else "Service levels and contractual penalties", sla[lang][1:])
        # long, realistic but irrelevant operational sections to push the key clauses apart
        topics_pl = ["Zarządzanie zmianą", "Kopie zapasowe", "Bezpieczeństwo informacji", "Raportowanie", "Szkolenia", "Audyt",
                     "Podwykonawcy", "Środowiska", "Zgłoszenia i priorytety", "Dokumentacja", "Wsparcie użytkowników", "Monitoring",
                     "Zarządzanie incydentami", "Zarządzanie problemami", "Ciągłość działania", "Licencje", "Wydajność", "Migracje",
                     "Odbiory", "Komunikacja"]
        topics_en = ["Change management", "Backups", "Information security", "Reporting", "Training", "Audit",
                     "Subcontractors", "Environments", "Tickets and priorities", "Documentation", "User support", "Monitoring",
                     "Incident management", "Problem management", "Business continuity", "Licences", "Performance", "Migrations",
                     "Acceptance", "Communication"]
        verbs_pl = ["Dostawca prowadzi rejestr", "Strony uzgadniają harmonogram", "Zamawiający może zażądać", "Dostawca przekazuje co miesiąc",
                    "Wszelkie odstępstwa wymagają akceptacji", "Dostawca zapewnia, że personel", "Strony dokonują przeglądu"]
        verbs_en = ["The Provider keeps a register", "The Parties agree a schedule", "The Customer may request", "The Provider submits monthly",
                    "Any deviations require approval", "The Provider ensures that staff", "The Parties review"]
        objs_pl = ["zmian w konfiguracji Systemu", "testów odtworzeniowych kopii zapasowych", "uprawnień użytkowników uprzywilejowanych",
                   "wskaźników jakości usług", "materiałów szkoleniowych", "wyników audytów bezpieczeństwa", "podwykonawców mających dostęp do danych",
                   "środowisk testowych i akceptacyjnych", "Zgłoszeń o priorytecie krytycznym", "procedur eksploatacyjnych"]
        objs_en = ["changes to the System configuration", "backup restore tests", "privileged user permissions", "service quality indicators",
                   "training materials", "security audit results", "subcontractors with access to data", "test and acceptance environments",
                   "critical-priority Tickets", "operating procedures"]
        tails_pl = ["w terminie 10 Dni Roboczych.", "nie rzadziej niż raz na kwartał.", "w formie elektronicznej.", "zgodnie z dobrymi praktykami ITIL.",
                    "z zachowaniem zasad z §11.", "po uprzednim uzgodnieniu terminu.", "i przechowuje dokumentację przez 3 lata."]
        tails_en = ["within 10 Business Days.", "at least once a quarter.", "in electronic form.", "in line with ITIL good practice.",
                    "subject to the rules in §11.", "after agreeing the date in advance.", "and keeps the records for 3 years."]
        T, V, O, X = (topics_pl, verbs_pl, objs_pl, tails_pl) if lang == "pl" else (topics_en, verbs_en, objs_en, tails_en)
        for i, topic in enumerate(T):
            paras = [f"{rng.choice(V)} {O[(i + j) % len(O)]} {rng.choice(X)}" for j in range(13)]
            section(topic, paras)
        for title, paras in CONTRACT_TAIL[lang]:
            section(title, paras)
        for title, paras in annexes[lang]:
            L.append(title)
            for p in paras:
                L.append(f"- {p}")
            L.append("")
        write(f"contract-{lang}.md", "\n".join(L))


# ======================================================================================== 3. meeting minutes
def build_minutes():
    # (meeting, op, id, text_pl, text_en)  ops: new, revoke, replace(old->new), reinstate, amend, reject_proposal
    D = [
        (1, "new", "D-01", "Budżet marketingu na IV kwartał: 180 000 zł.", "Marketing budget for Q4: PLN 180,000."),
        (1, "new", "D-02", "Wdrożenie CRM odkładamy do stycznia.", "CRM roll-out postponed to January."),
        (1, "new", "D-03", "Zespół wsparcia pracuje od 8:00 do 18:00.", "The support team works from 8:00 to 18:00."),
        (1, "new", "D-04", "Zakup 12 laptopów dla działu sprzedaży.", "Purchase of 12 laptops for the sales department."),
        (2, "new", "D-05", "Spotkania kwartalne odbywają się stacjonarnie w Krakowie.", "Quarterly meetings are held in person in Kraków."),
        (2, "revoke", "D-02", "Uchylamy D-02: CRM wdrażamy jednak w listopadzie.", "D-02 is revoked: the CRM will be rolled out in November after all."),
        (2, "new", "D-06", "CRM wdrażamy 15 listopada.", "The CRM goes live on 15 November."),
        (2, "new", "D-07", "Premia roczna wypłacana w marcu.", "The annual bonus is paid in March."),
        (3, "reject", "D-05", "Wniosek o uchylenie D-05 (spotkania online) odrzucono – D-05 obowiązuje.", "A motion to revoke D-05 (meetings online) was rejected – D-05 stays in force."),
        (3, "new", "D-08", "Nowy dostawca kawy od listopada.", "New coffee supplier from November."),
        (3, "replace", "D-04", "D-04 zastępujemy decyzją D-09.", "D-04 is replaced by D-09."),
        (3, "new", "D-09", "Leasing 12 laptopów zamiast zakupu.", "Leasing 12 laptops instead of buying them."),
        (4, "new", "D-10", "Praca zdalna do 3 dni w tygodniu.", "Remote work up to 3 days a week."),
        (4, "revoke", "D-07", "Uchylamy D-07.", "D-07 is revoked."),
        (4, "new", "D-11", "Premia roczna wypłacana w lutym.", "The annual bonus is paid in February."),
        (4, "amend", "D-01", "D-01 zmieniamy: budżet marketingu 150 000 zł (nadal obowiązuje).", "D-01 is amended: marketing budget PLN 150,000 (still in force)."),
        (5, "new", "D-12", "Zakaz lotów krajowych w podróżach służbowych.", "No domestic flights for business travel."),
        (5, "revoke", "D-08", "Uchylamy D-08 – zostajemy przy obecnym dostawcy.", "D-08 is revoked – we keep the current supplier."),
        (5, "new", "D-13", "Szkolenie z bezpieczeństwa obowiązkowe do końca roku.", "Security training mandatory by year end."),
        (6, "reinstate", "D-07", "Przywracamy D-07 i uchylamy D-11 (premia jednak w marcu).", "D-07 is reinstated and D-11 revoked (the bonus is paid in March after all)."),
        (6, "revoke", "D-11", "", ""),
        (6, "new", "D-14", "Biuro zamknięte 24 i 31 grudnia.", "The office is closed on 24 and 31 December."),
        (6, "replace", "D-10", "D-10 zastępujemy D-15.", "D-10 is replaced by D-15."),
        (6, "new", "D-15", "Praca zdalna do 2 dni w tygodniu.", "Remote work up to 2 days a week."),
        (7, "new", "D-16", "Nowy regulamin parkingu od stycznia.", "New parking rules from January."),
        (7, "postpone", "D-12", "Wniosek o uchylenie D-12 dla tras powyżej 600 km odłożono do kolejnego posiedzenia – D-12 obowiązuje.", "A motion to revoke D-12 for routes over 600 km was postponed to the next meeting – D-12 stays in force."),
        (8, "new", "D-17", "Zespół wsparcia pracuje od 7:00 do 19:00.", "The support team works from 7:00 to 19:00."),
        (8, "replace", "D-03", "D-03 zastępujemy D-17.", "D-03 is replaced by D-17."),
        (8, "revoke", "D-16", "Uchylamy D-16 – regulamin parkingu bez zmian.", "D-16 is revoked – parking rules unchanged."),
    ]
    in_force = {}
    for m, op, did, *_ in D:
        if op in ("new", "reinstate"):
            in_force[did] = True
        elif op in ("revoke", "replace"):
            in_force[did] = False
        # "reject" and "postpone": a motion that did NOT change anything (traps)
    ANSWERS["minutes"] = {"in_force": sorted(d for d, v in in_force.items() if v)}
    fill_pl = ["Dyskusja dotyczyła realizacji planu sprzedaży oraz opóźnień w dostawach od partnerów logistycznych.",
               "Przedstawiono raport z wykorzystania budżetu – odchylenia mieszczą się w zakładanym przedziale.",
               "Kierownik działu IT omówił stan migracji serwerów i ryzyka związane z harmonogramem.",
               "Zgłoszono potrzebę doprecyzowania zasad zastępstw w okresie urlopowym.",
               "Omówiono wyniki ankiety satysfakcji pracowników; szczegóły zostaną przesłane mailem.",
               "Przedstawiciel działu HR poinformował o rekrutacji na dwa stanowiska w zespole wsparcia."]
    fill_en = ["The discussion covered the sales plan and delays in deliveries from logistics partners.",
               "A budget utilisation report was presented – deviations are within the expected range.",
               "The head of IT summarised the server migration and the schedule risks.",
               "A need to clarify cover arrangements during the holiday season was raised.",
               "Results of the employee satisfaction survey were discussed; details will follow by email.",
               "HR announced recruitment for two positions in the support team."]
    dates = ["2026-07-06", "2026-07-20", "2026-08-03", "2026-08-17", "2026-08-31", "2026-09-14", "2026-09-28", "2026-10-05"]
    for lang in ("pl", "en"):
        rng = random.Random(1303)
        F = fill_pl if lang == "pl" else fill_en
        L = ["PROTOKOŁY POSIEDZEŃ ZARZĄDU – KWADRAT SP. Z O.O." if lang == "pl" else "MINUTES OF BOARD MEETINGS – KWADRAT SP. Z O.O.", ""]
        for m in range(1, 9):
            L += [f"## {'Posiedzenie' if lang == 'pl' else 'Meeting'} nr {m} – {dates[m - 1]}", "",
                  ("Obecni: A. Nowak, P. Zieliński, K. Wiśniewska, T. Wójcik." if lang == "pl" else
                   "Present: A. Nowak, P. Zieliński, K. Wiśniewska, T. Wójcik."), ""]
            for _ in range(16):
                L.append(" ".join(rng.sample(F, 4)))
                L.append("")
            L.append("**Ustalenia:**" if lang == "pl" else "**Resolutions:**")
            for mm, op, did, tpl, ten in D:
                if mm != m or not (tpl if lang == "pl" else ten):
                    continue
                text = tpl if lang == "pl" else ten
                L.append(f"- {did}: {text}" if op == "new" else f"- {text}")
            L.append("")
            for _ in range(4):
                L.append(" ".join(rng.sample(F, 2)))
                L.append("")
        write(f"minutes-{lang}.md", "\n".join(L))


# ======================================================================================== 4. financial report
def build_financial():
    seg = {"cloud": {"2025": [12.4, 13.1, 13.9], "2026": [15.2, 18.4, 17.3]},
           "licences": {"2025": [8.2, 7.9, 8.4], "2026": [7.6, 7.7, 7.1]},
           "support": {"2025": [5.1, 5.3, 5.2], "2026": [5.6, 5.4, 5.8]}}
    restated_q1_2025_cloud = 11.9   # Note 12
    one_off_q2_2026_cloud = 1.8     # Note 7
    c25 = restated_q1_2025_cloud + seg["cloud"]["2025"][1] + seg["cloud"]["2025"][2]
    c26 = sum(seg["cloud"]["2026"]) - one_off_q2_2026_cloud
    ANSWERS["financial"] = {"cloud_2025_restated": round(c25, 2), "cloud_2026_adjusted": round(c26, 2),
                            "growth_pct": round((c26 / c25 - 1) * 100, 1)}
    names = {"pl": {"cloud": "Usługi chmurowe", "licences": "Licencje", "support": "Wsparcie"},
             "en": {"cloud": "Cloud services", "licences": "Licences", "support": "Support"}}
    for lang in ("pl", "en"):
        rng = random.Random(1404)
        n = names[lang]
        dec = (lambda x: f"{x:.1f}".replace(".", ",")) if lang == "pl" else (lambda x: f"{x:.1f}")
        L = ["# Kwadrat S.A. – Raport za trzy kwartały 2026 r." if lang == "pl" else "# Kwadrat S.A. – Report for the first three quarters of 2026", ""]
        intro_pl = ["Grupa kontynuowała transformację w kierunku modelu subskrypcyjnego.", "Przychody ze sprzedaży licencji wieczystych spadały zgodnie z oczekiwaniami.",
                    "Zarząd utrzymuje prognozę marży operacyjnej na poziomie 14–16%.", "Wzrost kosztów energii w centrach danych był niższy niż zakładano.",
                    "W III kwartale pozyskano 42 nowych klientów korporacyjnych.", "Wskaźnik odejść klientów (churn) wyniósł 1,9% kwartalnie."]
        intro_en = ["The group continued its shift towards a subscription model.", "Perpetual licence revenue declined as expected.",
                    "The board keeps its operating margin guidance at 14–16%.", "Data-centre energy costs rose less than assumed.",
                    "42 new enterprise customers were signed in Q3.", "Customer churn was 1.9% per quarter."]
        I = intro_pl if lang == "pl" else intro_en
        for h in (["Komentarz Zarządu", "Otoczenie rynkowe", "Strategia", "Ryzyka", "Klienci", "Produkty", "Inwestycje", "ESG"] if lang == "pl" else
                  ["Management commentary", "Market environment", "Strategy", "Risks", "Customers", "Products", "Investments", "ESG"]):
            L += [f"## {h}", ""]
            for _ in range(9):
                L.append(" ".join(rng.sample(I, 4)))
                L.append("")
        L += ["## Przychody segmentów (mln zł)" if lang == "pl" else "## Segment revenue (PLN m)", "",
              "| Segment | Q1 2025 | Q2 2025 | Q3 2025 | Q1 2026 | Q2 2026 | Q3 2026 |", "|---|---|---|---|---|---|---|"]
        for k in ("cloud", "licences", "support"):
            L.append(f"| {n[k]} | " + " | ".join(dec(x) for x in seg[k]["2025"] + seg[k]["2026"]) + " |")
        L += ["", ("Dane za 2025 r. w tabeli powyżej są danymi pierwotnie opublikowanymi (przed przekształceniem)." if lang == "pl" else
                   "2025 figures in the table above are as originally published (before restatement)."), ""]
        for h in (["Koszty operacyjne", "Przepływy pieniężne", "Zatrudnienie"] if lang == "pl" else ["Operating costs", "Cash flow", "Headcount"]):
            L += [f"## {h}", ""]
            for _ in range(4):
                L.append(" ".join(rng.sample(I, 4)))
                L.append("")
        L += ["## Noty objaśniające" if lang == "pl" else "## Notes", ""]
        notes = []
        for i in range(1, 31):
            if i == 7:
                notes.append((f"Nota {i}. Zdarzenia jednorazowe" if lang == "pl" else f"Note {i}. One-off items",
                              (f"Przychody segmentu {n['cloud']} w II kwartale 2026 r. obejmują jednorazową opłatę za migrację klienta w kwocie {dec(one_off_q2_2026_cloud)} mln zł, "
                               "która nie powtórzy się w kolejnych okresach." if lang == "pl" else
                               f"{n['cloud']} revenue in Q2 2026 includes a one-off customer migration fee of PLN {dec(one_off_q2_2026_cloud)}m, "
                               "which will not recur in later periods.")))
            elif i == 12:
                notes.append((f"Nota {i}. Przekształcenie danych porównawczych" if lang == "pl" else f"Note {i}. Restatement of comparatives",
                              (f"W wyniku korekty rozpoznania przychodów przychody segmentu {n['cloud']} za I kwartał 2025 r. przekształcono z "
                               f"{dec(seg['cloud']['2025'][0])} do {dec(restated_q1_2025_cloud)} mln zł. Pozostałe kwartały nie uległy zmianie." if lang == "pl" else
                               f"Following a revenue recognition correction, {n['cloud']} revenue for Q1 2025 was restated from "
                               f"{dec(seg['cloud']['2025'][0])} to PLN {dec(restated_q1_2025_cloud)}m. Other quarters are unchanged.")))
            else:
                notes.append((f"Nota {i}" if lang == "pl" else f"Note {i}", " ".join(rng.sample(I, 5))))
        for h, t in notes:
            L += [f"### {h}", "", t, ""]
        write(f"financial-{lang}.md", "\n".join(L))


# ======================================================================================== 5. policy PL vs EN
def build_policy():
    rng = random.Random(1505)
    sections = []  # (num, pl, en_same_or_diff)
    base = [
        ("1.1", "Polityka dotyczy wszystkich pracowników i współpracowników.", "The policy applies to all employees and contractors."),
        ("1.2", "Podróż służbową zleca bezpośredni przełożony.", "Business travel is ordered by the direct manager."),
        ("2.1", "Bilety kolejowe kupuje się w klasie 2.", "Train tickets are bought in 2nd class."),
        ("2.2", "Podróże samolotem wymagają zgody dyrektora.", "Air travel requires the director's approval."),
        ("3.1", "Nocleg rezerwuje dział administracji.", "Accommodation is booked by the administration department."),
        ("3.2", "Limit za nocleg w Polsce wynosi 450 zł za dobę.", "The accommodation limit in Poland is PLN 500 per night."),       # DIFF
        ("3.3", "Śniadanie wliczone w cenę noclegu pomniejsza dietę o 25%.", "Breakfast included in the room rate reduces the per diem by 25%."),
        ("4.1", "Samochód prywatny wymaga umowy na jego używanie.", "Using a private car requires a usage agreement."),
        ("4.2", "Stawka za kilometr wynosi 1,15 zł.", "The mileage rate is PLN 1.15 per km."),
        ("5.1", "Rozliczenie podróży składa się w ciągu 14 dni od jej zakończenia.", "The trip settlement is filed within 30 days of its end."),   # DIFF
        ("5.2", "Do rozliczenia dołącza się oryginały lub skany faktur.", "Originals or scans of invoices are attached to the settlement."),
        ("6.1", "Zaliczkę wypłaca się na 3 dni przed podróżą.", "Advances are paid 3 days before the trip."),
        ("6.2", "Niewykorzystaną zaliczkę zwraca się w ciągu 7 dni.", "Unused advances are returned within 7 days."),
        ("7.1", "Dieta krajowa wynosi 45 zł.", "The domestic per diem is PLN 45."),
        ("7.2", "Przy podróży krótszej niż 8 godzin dieta nie przysługuje.", "No per diem is due for trips shorter than 8 hours."),
        ("7.3", "Diety zagraniczne określa załącznik do rozporządzenia.", "Foreign per diems are set out in the annex to the regulation."),
        ("7.4", "Dieta w Niemczech wynosi 49 euro.", "The per diem in Germany is EUR 54."),                                              # DIFF
        ("8.1", "Koszty reprezentacji wymagają podania uczestników.", "Hospitality costs require listing the participants."),
        ("8.2", "Napiwki nie podlegają zwrotowi.", "Tips are not reimbursed."),
        ("9.1", "Wydatki powyżej 2 000 zł wymagają zgody dyrektora finansowego.", "Expenses above PLN 3,000 require the CFO's approval."),   # DIFF
        ("9.2", "Polityka wchodzi w życie 1 października 2026 r.", "The policy takes effect on 1 October 2026."),
        ("9.3", "Pytania dotyczące polityki kieruje się do działu finansów.", "Questions about the policy go to the finance department."),
    ]
    # cosmetic (non-substantive) differences: wording only, same meaning
    cosmetic_en = {"2.1": "Train tickets must be purchased in second class.", "8.2": "Gratuities are not refundable."}
    ANSWERS["policy"] = {"differ": ["3.2", "5.1", "7.4", "9.1"]}
    fill_pl = ["Postanowienie stosuje się odpowiednio do współpracowników.", "Wyjątki od zasady zatwierdza dział finansów.",
               "W razie wątpliwości stosuje się przepisy prawa pracy.", "Dział administracji prowadzi ewidencję w systemie HR."]
    fill_en = ["The provision applies accordingly to contractors.", "Exceptions are approved by the finance department.",
               "In case of doubt, labour law applies.", "The administration department keeps records in the HR system."]
    extra_pl = ["Wniosek o podróż składa się w systemie HR.", "Rezerwacji dokonuje się z co najmniej 7-dniowym wyprzedzeniem.",
                "Koszty parkingu podlegają zwrotowi na podstawie paragonu.", "Taksówki dopuszcza się, gdy brak komunikacji publicznej.",
                "Ubezpieczenie podróży zapewnia pracodawca.", "Program lojalnościowy linii lotniczych nie wpływa na wybór przewoźnika.",
                "Faktury wystawia się na Kwadrat Sp. z o.o.", "Zmiany rezerwacji zgłasza się działowi administracji.",
                "Podróże w weekend wymagają uzasadnienia.", "Koszty prania odzieży nie podlegają zwrotowi."]
    extra_en = ["Travel requests are filed in the HR system.", "Bookings are made at least 7 days in advance.",
                "Parking costs are reimbursed against a receipt.", "Taxis are allowed when no public transport is available.",
                "Travel insurance is provided by the employer.", "Airline loyalty programmes do not influence the choice of carrier.",
                "Invoices are issued to Kwadrat Sp. z o.o.", "Booking changes are reported to the administration department.",
                "Weekend travel requires a justification.", "Laundry costs are not reimbursed."]
    for chap in range(10, 22):
        for sub in range(1, 6):
            j = (chap * 7 + sub * 3) % len(extra_pl)
            base.append((f"{chap}.{sub}", extra_pl[j], extra_en[j]))
    pl, en = ["# Polityka podróży służbowych (wersja polska)", ""], ["# Business travel policy (English version)", ""]
    for num, tpl, ten in base:
        extra = rng.randrange(1, 4)
        idx = rng.sample(range(4), extra)
        pl.append(f"§{num} {tpl} " + " ".join(fill_pl[i] for i in idx))
        en.append(f"§{num} {cosmetic_en.get(num, ten)} " + " ".join(fill_en[i] for i in idx))
        pl.append("")
        en.append("")
    write("policy-pl.md", "\n".join(pl))
    write("policy-en.md", "\n".join(en))


# ======================================================================================== 6. email thread (action items)
def build_thread():
    people = {"anna": "Anna Nowak", "piotr": "Piotr Zieliński", "kasia": "Katarzyna Wiśniewska", "tomek": "Tomasz Wójcik", "ola": "Aleksandra Mazur"}
    # (day, from, op, item, owner/arg)  ops: assign, reassign, done, cancel, reopen
    ev = [(1, "anna", "assign", "A-01", "piotr"), (1, "anna", "assign", "A-02", "kasia"), (1, "anna", "assign", "A-03", "tomek"),
          (2, "piotr", "assign", "A-04", "ola"), (3, "kasia", "done", "A-02", None), (3, "anna", "assign", "A-05", "piotr"),
          (4, "tomek", "reassign", "A-03", "ola"), (5, "ola", "assign", "A-06", "anna"), (6, "piotr", "done", "A-01", None),
          (7, "anna", "cancel", "A-04", None), (8, "anna", "assign", "A-07", "kasia"), (9, "kasia", "reassign", "A-07", "tomek"),
          (10, "ola", "done", "A-03", None), (11, "anna", "reopen", "A-03", None), (12, "anna", "assign", "A-08", "ola"),
          (13, "tomek", "reassign", "A-07", "piotr"), (14, "anna", "assign", "A-09", "tomek"), (15, "piotr", "done", "A-05", None),
          (16, "ola", "done", "A-08", None), (17, "anna", "assign", "A-10", "kasia"), (18, "kasia", "cancel", "A-10", None),
          (19, "anna", "assign", "A-11", "anna")]
    owner, status = {}, {}
    for day, frm, op, item, arg in ev:
        if op == "assign":
            owner[item], status[item] = arg, "open"
        elif op == "reassign":
            owner[item] = arg
        elif op == "done":
            status[item] = "done"
        elif op == "cancel":
            status[item] = "cancelled"
        elif op == "reopen":
            status[item] = "open"
    ANSWERS["thread"] = {"open": sorted(i for i, s in status.items() if s == "open"),
                         "owner_A-07": people[owner["A-07"]], "owner_A-03": people[owner["A-03"]]}
    tasks = {"A-01": ("przygotować ofertę dla klienta Lumen", "prepare the offer for client Lumen"),
             "A-02": ("zaktualizować cennik", "update the price list"), "A-03": ("umówić audyt bezpieczeństwa", "schedule the security audit"),
             "A-04": ("zamówić stoisko na targi", "book a trade-fair stand"), "A-05": ("podsumować wyniki kampanii", "summarise the campaign results"),
             "A-06": ("sprawdzić umowę najmu", "review the lease agreement"), "A-07": ("przygotować prezentację dla zarządu", "prepare the board presentation"),
             "A-08": ("odnowić licencje antywirusowe", "renew the antivirus licences"), "A-09": ("przeprowadzić inwentaryzację sprzętu", "run the hardware inventory"),
             "A-10": ("zorganizować szkolenie z Excela", "organise Excel training"), "A-11": ("zatwierdzić budżet na 2027 r.", "approve the 2027 budget")}
    chatter_pl = ["Dzięki za szybką odpowiedź.", "Wracam do tematu po spotkaniu z klientem.", "W załączniku przesyłam notatki.",
                  "Czy możemy przesunąć rozmowę na czwartek?", "Potwierdzam, wszystko jasne.", "Proszę o informację, jeśli coś się zmieni."]
    chatter_en = ["Thanks for the quick reply.", "Coming back to this after the client meeting.", "Notes attached.",
                  "Can we move the call to Thursday?", "Confirmed, all clear.", "Please let me know if anything changes."]
    for lang in ("pl", "en"):
        rng = random.Random(1606)
        C = chatter_pl if lang == "pl" else chatter_en
        L = ["# Wątek: Projekt „Atlas” – zadania (wrzesień 2026)" if lang == "pl" else "# Thread: Project \"Atlas\" – action items (September 2026)", ""]
        n = 0
        for day in range(1, 22):
            for frm, op, item, arg in [(e[1], e[2], e[3], e[4]) for e in ev if e[0] == day] + [(rng.choice(list(people)), "chat", None, None) for _ in range(rng.randrange(3, 7))]:
                n += 1
                t = tasks.get(item, ("", ""))
                if op == "chat":
                    body = " ".join(rng.sample(C, 3))
                else:
                    who = people.get(arg, "")
                    pl_txt = {"assign": lambda: f"{item}: {who.split()[0]}, proszę {t[0]}.",
                              "reassign": lambda: f"{item} przejmuje {who} – ja nie dam rady.",
                              "done": lambda: f"{item} zrobione ({t[0]}).", "cancel": lambda: f"{item} anulujemy, nie jest już potrzebne.",
                              "reopen": lambda: f"{item} otwieram ponownie – audytor odwołał termin."}
                    en_txt = {"assign": lambda: f"{item}: {who.split()[0]}, please {t[1]}.",
                              "reassign": lambda: f"{who} takes over {item} – I can't make it.",
                              "done": lambda: f"{item} done ({t[1]}).", "cancel": lambda: f"{item} is cancelled, no longer needed.",
                              "reopen": lambda: f"Reopening {item} – the auditor cancelled the date."}
                    body = (pl_txt if lang == "pl" else en_txt)[op]() + " " + rng.choice(C)
                quote = (" > " + " ".join(rng.sample(C, 2))) if op == "chat" and rng.random() < 0.5 else ""
                L += [f"**#{n} – 2026-09-{day:02d} – {people[frm]}**", body + quote, ""]
        write(f"thread-{lang}.md", "\n".join(L))


# ======================================================================================== 7. regulation + amendments
def build_regulation():
    ANSWERS["regulation"] = {"days": 21, "oncall_days": 4, "rate": 12, "cap": 220, "oncall": 5,
                             "answer_pln": min(21 * 12, 220) + 4 * 5}
    pl = """# Regulamin pracy zdalnej – Kwadrat Sp. z o.o.

## Tekst pierwotny (obowiązuje od 1 kwietnia 2024 r.)

§1. Regulamin określa zasady wykonywania pracy zdalnej oraz wypłaty ryczałtu za pracę zdalną.
§2. Ryczałt wynosi 8 zł za każdy dzień pracy zdalnej i obejmuje koszty energii elektrycznej oraz usług telekomunikacyjnych.
§3. Ryczałt wypłaca się z wynagrodzeniem za miesiąc, w którym praca była wykonywana.
§4. Dniem pracy zdalnej jest dzień, w którym pracownik wykonywał pracę zdalnie przez co najmniej 6 godzin.
§5. Pracownik ewidencjonuje dni pracy zdalnej w systemie HR do 3. dnia następnego miesiąca.

## Aneks nr 1 (obowiązuje od 1 marca 2025 r.)

§2 otrzymuje brzmienie: „Ryczałt wynosi 10 zł za każdy dzień pracy zdalnej.”

## Aneks nr 2 (obowiązuje od 1 lipca 2026 r.)

1. §2 otrzymuje brzmienie: „Ryczałt wynosi 12 zł za każdy dzień pracy zdalnej, nie więcej niż 220 zł miesięcznie.”
2. Dodaje się §2a: „Za każdy dzień pracy zdalnej, w którym pracownik pełni dyżur pod telefonem, przysługuje dodatek 5 zł. Limit z §2 nie dotyczy dodatku.”

## Aneks nr 3 (obowiązuje od 1 stycznia 2027 r.)

§2 otrzymuje brzmienie: „Ryczałt wynosi 15 zł za każdy dzień pracy zdalnej, nie więcej niż 300 zł miesięcznie.”
"""
    en = """# Remote work regulation – Kwadrat Sp. z o.o.

## Original text (in force from 1 April 2024)

§1. This regulation sets out the rules for remote work and the remote-work allowance.
§2. The allowance is PLN 8 for each day of remote work and covers electricity and telecommunication costs.
§3. The allowance is paid with the salary for the month in which the work was done.
§4. A remote work day is a day on which the employee worked remotely for at least 6 hours.
§5. Employees record remote work days in the HR system by the 3rd day of the following month.

## Amendment No. 1 (in force from 1 March 2025)

§2 is replaced by: "The allowance is PLN 10 for each day of remote work."

## Amendment No. 2 (in force from 1 July 2026)

1. §2 is replaced by: "The allowance is PLN 12 for each day of remote work, up to PLN 220 per month."
2. §2a is added: "For each remote work day on which the employee is on phone on-call duty, a supplement of PLN 5 is due. The limit in §2 does not apply to the supplement."

## Amendment No. 3 (in force from 1 January 2027)

§2 is replaced by: "The allowance is PLN 15 for each day of remote work, up to PLN 300 per month."
"""
    # pad with a long FAQ so the amendments are not adjacent to the question
    faq_pl, faq_en = [], []
    qs = [("Czy ryczałt przysługuje w czasie urlopu?", "Nie, ryczałt przysługuje wyłącznie za dni pracy zdalnej.", "Is the allowance paid during leave?", "No, it is paid only for remote work days."),
          ("Czy można pracować zdalnie z zagranicy?", "Tak, po uzyskaniu zgody przełożonego i działu HR.", "Can I work remotely from abroad?", "Yes, with approval from the manager and HR."),
          ("Kto zapewnia sprzęt?", "Pracodawca zapewnia laptop i monitor.", "Who provides equipment?", "The employer provides a laptop and a monitor."),
          ("Jak zgłosić awarię sprzętu?", "Przez portal serwisowy IT.", "How do I report equipment failure?", "Through the IT service portal."),
          ("Czy dzień częściowo zdalny się liczy?", "Tylko jeśli praca zdalna trwała co najmniej 6 godzin (§4).", "Does a partly remote day count?", "Only if remote work lasted at least 6 hours (§4)."),
          ("Kiedy ewidencjonować dni?", "Do 3. dnia następnego miesiąca (§5).", "When do I record the days?", "By the 3rd day of the following month (§5).")]
    rng = random.Random(1707)
    for i in range(140):
        q = qs[i % len(qs)]
        faq_pl.append(f"**Pytanie {i + 1}. {q[0]}** {q[1]} " + rng.choice(["Szczegóły w intranecie.", "Wyjaśnienia udziela dział HR.", "Zasada obowiązuje bez wyjątków."]))
        faq_en.append(f"**Question {i + 1}. {q[2]}** {q[3]} " + rng.choice(["Details on the intranet.", "HR can explain further.", "The rule has no exceptions."]))
    write("regulation-pl.md", pl + "\n## Najczęstsze pytania\n\n" + "\n\n".join(faq_pl))
    write("regulation-en.md", en + "\n## Frequently asked questions\n\n" + "\n\n".join(faq_en))


# ======================================================================================== 8. orders vs invoices
def build_reconciliation():
    rng = random.Random(1808)
    orders = [(f"Z-{i:04d}", round(rng.uniform(120, 9800), 2)) for i in range(1, 601)]
    inv = []
    issues = {"missing": [], "mismatch": []}
    split_orders = {17, 64, 133, 402, 555}
    tiny_diff = {41, 98, 287, 510}
    missing = {22, 77, 150, 366}
    mismatch = {9, 58, 121, 144, 478}
    n = 0
    for i, (oid, amt) in enumerate(orders, 1):
        if i in missing:
            issues["missing"].append(oid)
            continue
        if i in split_orders:  # two partial invoices summing to the order amount: NOT an issue
            a = round(amt * 0.4, 2)
            for part in (a, round(amt - a, 2)):
                n += 1
                inv.append((f"FV/2026/{n:04d}", oid, part))
            continue
        val = amt
        if i in tiny_diff:  # within the 1.00 zł tolerance: NOT an issue
            val = round(amt + 0.8, 2)
        if i in mismatch:
            val = round(amt + rng.choice([-1, 1]) * rng.uniform(12, 400), 2)
            issues["mismatch"].append(oid)
        n += 1
        inv.append((f"FV/2026/{n:04d}", oid, val))
    rng.shuffle(inv)
    ANSWERS["reconciliation"] = {"follow_up": sorted(issues["missing"] + issues["mismatch"]), **issues}
    o = io.StringIO()
    w = csv.writer(o, lineterminator="\n")
    w.writerow(["order_id", "amount_pln"])
    for oid, amt in orders:
        w.writerow([oid, f"{amt:.2f}"])
    i2 = io.StringIO()
    w = csv.writer(i2, lineterminator="\n")
    w.writerow(["invoice_no", "order_id", "amount_pln"])
    for row in inv:
        w.writerow([row[0], row[1], f"{row[2]:.2f}"])
    write("orders.csv", o.getvalue())
    write("invoices.csv", i2.getvalue())


if __name__ == "__main__":
    for fn in (build_log, build_contract, build_minutes, build_financial, build_policy, build_thread, build_regulation, build_reconciliation):
        fn()
    with open(os.path.join(OUT, "answers.json"), "w", encoding="utf-8") as f:
        json.dump(ANSWERS, f, ensure_ascii=False, indent=1)
        f.write("\n")
    for k, v in ANSWERS.items():
        print(k, v)
