"""Canonical schema: the common vocabulary every platform is mapped onto."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CField:
    name: str
    dtype: str  # text name phone email vin license state date year enum id plate
    label: str
    required: bool = False
    key: bool = False  # strong identifier: used for duplicates and identity resolution
    categorical: bool = False  # spelling check against frequent values
    allowed: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = field(default_factory=tuple)


STATUS = ("active", "inactive", "pending", "terminated")

ENTITIES: dict[str, list[CField]] = {
    "driver": [
        CField("full_name", "name", "Driver name", required=True,
               synonyms=("name", "driver", "driver name", "full name", "operator", "employee name", "driver full name")),
        CField("employee_id", "id", "Employee ID", key=True,
               synonyms=("employee id", "emp id", "employee number", "driver id", "external id", "username", "staff id")),
        CField("license_number", "license", "License number", required=True, key=True,
               synonyms=("license", "license number", "licence number", "cdl", "cdl number", "cdl #", "dl number", "driver license", "licensenumber")),
        CField("license_state", "state", "License state",
               synonyms=("license state", "state", "dl state", "cdl state", "licensestate")),
        CField("phone", "phone", "Phone", synonyms=("phone", "phone number", "mobile", "cell", "contact number")),
        CField("email", "email", "Email", key=True, synonyms=("email", "e-mail", "email address")),
        CField("status", "enum", "Status", allowed=STATUS, categorical=True,
               synonyms=("status", "driver status", "activation status", "driveractivationstatus", "employment status")),
        CField("hire_date", "date", "Hire / start date", synonyms=("hire date", "start date", "date hired", "onboarding date", "joined")),
        CField("assigned_vehicle", "id", "Assigned vehicle",
               synonyms=("vehicle", "assigned vehicle", "unit", "truck", "truck number", "static assigned driver", "staticassignedvehicle.name")),
    ],
    "vehicle": [
        CField("vin", "vin", "VIN", required=True, key=True, synonyms=("vin", "vehicle identification number", "vin number")),
        CField("unit_number", "id", "Unit number", required=True, key=True,
               synonyms=("unit", "unit number", "unit #", "truck number", "vehicle name", "name", "fleet number", "truck")),
        CField("license_plate", "plate", "License plate", key=True,
               synonyms=("plate", "license plate", "licenseplate", "tag", "plate number")),
        CField("make", "text", "Make", categorical=True, synonyms=("make", "manufacturer", "brand")),
        CField("model", "text", "Model", categorical=True, synonyms=("model", "vehicle model")),
        CField("year", "year", "Year", synonyms=("year", "model year", "mfg year")),
        CField("status", "enum", "Status", allowed=("active", "inactive", "maintenance", "sold"), categorical=True,
               synonyms=("status", "vehicle status")),
        CField("assigned_driver", "name", "Assigned driver",
               synonyms=("driver", "assigned driver", "static assigned driver.name", "staticassigneddriver.name", "operator")),
    ],
}


def fields_of(entity: str) -> list[CField]:
    return ENTITIES[entity]


def field_map(entity: str) -> dict[str, CField]:
    return {f.name: f for f in ENTITIES[entity]}
