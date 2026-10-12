"""TDAC 团体旅客的本地资料模型；不包含任何网页自动化。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

SENSITIVE_KEYS = ("passportNo", "phoneNo")
REQUIRED_PROFILE_KEYS = (
    "familyName", "firstName", "nationality", "dateOfBirth", "gender", "occupation",
    "countryOfResidence", "cityOfResidence", "phoneCode",
)


@dataclass(frozen=True)
class Traveler:
    id: str
    familyName: str
    firstName: str
    middleName: str
    nationality: str
    dateOfBirth: str
    gender: str
    occupation: str
    countryOfResidence: str
    cityOfResidence: str
    phoneCode: str
    visaNo: str
    passportNo: str
    phoneNo: str
    sharePreviousTravel: bool = False

    def profile(self) -> dict[str, str]:
        return {key: str(getattr(self, key, "")) for key in (
            "familyName", "firstName", "middleName", "nationality", "dateOfBirth", "gender",
            "occupation", "countryOfResidence", "cityOfResidence", "phoneCode", "visaNo",
            "passportNo", "phoneNo",
        )}


@dataclass(frozen=True)
class GroupConfig:
    travelers: list[Traveler]
    receiverEmail: str = ""


def validate_group(group: GroupConfig) -> list[str]:
    if not group.travelers:
        return ["团体至少需要 1 名旅客"]
    if len(group.travelers) > 10:
        return [f"团体最多 10 名旅客，当前为 {len(group.travelers)} 名"]
    errors: list[str] = []
    seen: set[str] = set()
    for t in group.travelers:
        if not t.id.strip():
            errors.append("旅客缺少稳定 ID")
            continue
        if t.id in seen:
            errors.append(f"{t.id}: 旅客 ID 重复")
        seen.add(t.id)
        for key in REQUIRED_PROFILE_KEYS:
            if not str(getattr(t, key, "")).strip():
                errors.append(f"{t.id}: 缺少资料 {key}")
        for key in SENSITIVE_KEYS:
            if not str(getattr(t, key, "")).strip():
                errors.append(f"{t.id}: 缺少敏感资料 {key}")
    return errors


def load_group_config(config_dir: str, secret_loader: Callable[[], dict[str, dict[str, str]]]) -> GroupConfig:
    path = os.path.join(config_dir, "group.json")
    with open(path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = json.load(fh)
    secrets = secret_loader()
    travelers: list[Traveler] = []
    for item in raw.get("travelers", []):
        identifier = str(item.get("id", "")).strip()
        secure = secrets.get(identifier, {}) if identifier else {}
        travelers.append(Traveler(
            id=identifier,
            familyName=str(item.get("familyName", "")), firstName=str(item.get("firstName", "")),
            middleName=str(item.get("middleName", "")), nationality=str(item.get("nationality", "")),
            dateOfBirth=str(item.get("dateOfBirth", "")), gender=str(item.get("gender", "")),
            occupation=str(item.get("occupation", "")), countryOfResidence=str(item.get("countryOfResidence", "")),
            cityOfResidence=str(item.get("cityOfResidence", "")), phoneCode=str(item.get("phoneCode", "")),
            visaNo=str(item.get("visaNo", "")), passportNo=str(secure.get("passportNo", "")),
            phoneNo=str(secure.get("phoneNo", "")), sharePreviousTravel=bool(item.get("sharePreviousTravel", False)),
        ))
    return GroupConfig(travelers, str(raw.get("receiverEmail", "")))


def should_use_previous_trip(index: int, requested: bool, site_offers_option: bool) -> bool:
    return index > 0 and requested and site_offers_option


def compare_preview(actual: dict[str, str], expected: dict[str, str]) -> list[str]:
    labels = {"arrivalFlightNo": "入境航班号", "departureFlightNo": "出境航班号", "arrivalDate": "入境日期", "departureDate": "离境日期", "accommodationPostalCode": "邮政编码"}
    return [f"{labels.get(key, key)}不一致" for key, value in expected.items() if value and str(actual.get(key, "")).strip().upper() != str(value).strip().upper()]


def preview_report(traveler: Traveler, trip: dict[str, str]) -> str:
    """生成供人工核对的脱敏摘要；故意不读取敏感字段或健康资料。"""
    rows = [
        f"旅客：{traveler.familyName} {traveler.firstName}".strip(),
        f"入境日期：{trip.get('arrivalDate', '')}",
        f"离境日期：{trip.get('departureDate', '')}",
        f"入境航班：{trip.get('arrivalFlightNo', '')}",
        f"出境航班：{trip.get('departureFlightNo', '')}",
        f"酒店：{trip.get('hotelName', '')}",
        f"邮编：{trip.get('accommodationPostalCode', '')}",
    ]
    return "\n".join(row for row in rows if row.split("：", 1)[-1])
