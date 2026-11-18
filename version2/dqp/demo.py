"""Demo data with deliberate defects so every rule has something to find. Safe to load: no network."""
from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

from .engine import auto_map, sync_and_validate
from .store import Store

FIRST = "James Maria Robert Linda Michael Susan David Karen Carlos Angela Peter Nina Omar Grace Tony Elena Victor Hannah Luis Priya Marcus Joan Ivan Chloe Dmitri Tasha Hector Bianca Walter Fatima Andre Olga Ray Leila Sam Wendy Noah Irene Kyle Paula".split()
LAST = "Smith Johnson Garcia Brown Davis Miller Wilson Moore Taylor Anderson Thomas Jackson White Harris Martin Lopez Lee Clark Lewis Walker Hall Allen Young King Wright Scott Green Baker Adams Nelson Hill Ramirez Campbell Mitchell Roberts Carter Phillips Evans Turner Torres Parker".split()
MAKES = ["Freightliner", "Kenworth", "Peterbilt", "Volvo", "International"]
MODELS = {"Freightliner": "Cascadia", "Kenworth": "T680", "Peterbilt": "579", "Volvo": "VNL 860", "International": "LT"}
VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


def _people(rng: random.Random, n: int = 40) -> list[dict]:
    out = []
    for i in range(n):
        first, last = FIRST[i], LAST[i]
        out.append({"name": f"{first} {last}", "emp": f"E{1000 + i}", "lic": f"{rng.choice('DKMTW')}{rng.randint(1000000, 9999999)}",
                    "state": rng.choice(["IL", "IN", "WI", "OH", "TX"]), "phone": f"{rng.randint(200, 999)}{rng.randint(200, 999)}{rng.randint(1000, 9999)}",
                    "email": f"{first}.{last}@example.com".lower(), "start": f"20{rng.randint(15, 25)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                    "unit": f"T{100 + i}", "status": rng.choice(["Active"] * 6 + ["Inactive"])})
    return out


def _vehicles(rng: random.Random, people: list[dict]) -> list[dict]:
    out = []
    for i, p in enumerate(people[:30]):
        make = rng.choice(MAKES)
        out.append({"unit": p["unit"], "vin": "".join(rng.choice(VIN_CHARS) for _ in range(17)), "plate": f"{rng.choice('ABCDEFG')}{rng.choice('ABCDEFG')}{rng.randint(1000, 9999)}",
                    "make": make, "model": MODELS[make], "year": rng.randint(2016, 2024), "status": "Active", "driver": p["name"]})
    return out


def build_demo(directory: str = "data/demo") -> dict:
    rng = random.Random(7)
    people = _people(rng)
    vehicles = _vehicles(rng, people)
    Path(directory).mkdir(parents=True, exist_ok=True)

    # --- Excel (driver + safety workbook): the "master" list, with human errors
    xl = [{"Driver Name": p["name"], "Employee ID": p["emp"], "CDL #": p["lic"], "License State": p["state"], "Phone": p["phone"],
           "Email": p["email"], "Status": p["status"], "Hire Date": p["start"], "Truck": p["unit"]} for p in people[:36]]
    xl[2]["Phone"] = "(555) 12-34"             # bad format
    xl[5]["CDL #"] = None                      # missing required
    n8 = xl[8]["Driver Name"]; xl[8]["Driver Name"] = n8[:2] + n8[3] + n8[2] + n8[4:]   # transposed letters
    xl[11]["Status"] = "Actve"                 # misspelled status
    xl[14]["Hire Date"] = "31/31/2020"         # impossible date
    xl[17]["CDL #"] = xl[16]["CDL #"]          # duplicate license
    xl.append(dict(xl[20]))                    # exact duplicate row
    xl.append({**xl[22], "Driver Name": xl[22]["Driver Name"].replace("a", "e", 1), "Employee ID": None})  # near-duplicate name
    veh_x = [{"Unit #": v["unit"], "VIN": v["vin"], "Plate": v["plate"], "Make": v["make"], "Model": v["model"], "Year": v["year"], "Status": v["status"], "Driver": v["driver"]} for v in vehicles[:28]]
    veh_x[3]["VIN"] = veh_x[3]["VIN"][:-1]     # 16-character VIN
    m6 = veh_x[6]["Make"]; veh_x[6]["Make"] = m6[:3] + m6[4] + m6[3] + m6[5:]   # transposed letters in make
    with pd.ExcelWriter(f"{directory}/drivers_safety.xlsx") as w:
        pd.DataFrame(xl).to_excel(w, sheet_name="Drivers", index=False)
        pd.DataFrame(veh_x).to_excel(w, sheet_name="Vehicles", index=False)

    # --- Monday "new drivers" board: only recent hires, different column titles
    mon = [{"Name": p["name"], "CDL Number": p["lic"], "Mobile": p["phone"], "Start Date": p["start"], "Driver Status": p["status"],
            "Assigned Truck": p["unit"]} for p in people[20:]]
    mon[1]["CDL Number"] = mon[1]["CDL Number"][:-2] + mon[1]["CDL Number"][-1] + mon[1]["CDL Number"][-2]   # swapped digits
    mon[4]["Mobile"] = None
    mon.append({"Name": "Zoe Ng", "CDL Number": "Z7654321", "Mobile": "3125550142", "Start Date": "2026-10-01", "Driver Status": "Pending", "Assigned Truck": None})  # only in Monday

    # --- Samsara: nested API shape, flattened by the connector
    sam = [{"name": p["name"], "username": p["emp"], "licenseNumber": p["lic"], "licenseState": p["state"], "phone": p["phone"],
            "driverActivationStatus": p["status"].lower(), "staticAssignedVehicle.name": p["unit"]} for p in people[:34]]
    sam[9]["phone"] = "+1 " + sam[9]["phone"]          # same number, different format: must NOT be flagged
    sam[12]["licenseState"] = "ZZ"
    sam_v = [{"name": v["unit"], "vin": v["vin"], "licensePlate": v["plate"], "make": v["make"], "model": v["model"], "year": v["year"]} for v in vehicles]
    m10 = sam_v[10]["make"]; sam_v[10]["make"] = m10[:-2] + m10[-1] + m10[-2]
    sam_v[15]["licensePlate"] = None

    # --- Cogoeld (ELD): CSV export
    eld = [{"Driver": f"{p['name'].split()[1]}, {p['name'].split()[0]}", "Driver ID": p["emp"], "License": p["lic"], "State": p["state"],
            "Phone Number": p["phone"], "Status": p["status"]} for p in people[3:36]]   # "Last, First" format
    eld[4]["License"] = "D" + eld[4]["License"][1:-1] + "0"   # license typo
    pd.DataFrame(eld).to_csv(f"{directory}/cogoeld_drivers.csv", index=False)

    return {"xlsx": f"{directory}/drivers_safety.xlsx", "csv": f"{directory}/cogoeld_drivers.csv",
            "monday": {"drivers": mon}, "samsara": {"drivers": sam, "vehicles": sam_v}}


def load_demo(store: Store, directory: str = "data/demo") -> dict:
    d = build_demo(directory)
    store.add_source("Excel – Drivers & Safety", "excel", {"path": d["xlsx"]})
    store.add_source("Monday – New drivers", "static", {"entities": d["monday"]})
    store.add_source("Samsara", "static", {"entities": d["samsara"]})
    store.add_source("Cogoeld ELD", "excel", {"path": d["csv"]})
    summary = sync_and_validate(store)
    return summary
