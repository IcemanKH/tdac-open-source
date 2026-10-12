# -*- coding: utf-8 -*-
"""tdac_synth.py — 完全合成的测试资料（A5）。

这里的每个字段都是当场构造出来的，**绝不**从真实 config/ 读取或"读入后
只替换部分字段"。演练与离线检查一律使用本模块，确保真实个人资料不会
进入自动化流程、官网或导出文件。

本文件不含任何敏感数据。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

# 合成资料中允许出现的邮箱域名（.invalid 为保留域名，永远不可投递）
SYNTHETIC_EMAIL = "tester@example.invalid"
SYNTHETIC_PASSPORT = "E00000000"
SYNTHETIC_PHONE = "13000000000"


def synthetic_profile() -> Dict[str, Any]:
    return {
        "familyName": "TESTFAMILY",
        "firstName": "TESTGIVEN",
        "middleName": "",
        "nationality": "CHINESE",
        "dateOfBirth": "1990/01/01",
        "gender": "MALE",
        "occupation": "EMPLOYEE",
        "countryOfResidence": "CHINA",
        "cityOfResidence": "TESTCITY",
        "phoneCode": "86",
        "visaNo": "",
        "email": SYNTHETIC_EMAIL,
        "passportNo": SYNTHETIC_PASSPORT,
        "phoneNo": SYNTHETIC_PHONE,
    }


def synthetic_trip(today: Optional[dt.date] = None) -> Dict[str, Any]:
    arrival = today or dt.date.today()
    departure = arrival + dt.timedelta(days=7)
    return {
        "arrivalDate": arrival.strftime("%Y/%m/%d"),
        "departureDate": departure.strftime("%Y/%m/%d"),
        "countryOfBoarding": "CHINA",
        "purposeOfTravel": "HOLIDAY",
        "arrivalMode": "AIR",
        "arrivalFlightType": "COMMERCIAL FLIGHT",
        "arrivalFlightNo": "MU0000",
        "departureMode": "AIR",
        "departureFlightType": "COMMERCIAL FLIGHT",
        "departureFlightNo": "MU0001",
        "accommodationType": "HOTEL",
        "accommodationProvince": "TESTPROVINCE",
        "accommodationDistrict": "TESTDISTRICT",
        "accommodationSubdistrict": "TESTSUBDISTRICT",
        "accommodationPostalCode": "00000",
        "accommodationAddress": "1 TEST ROAD, TESTCITY",
        "hotelName": "TEST HOTEL",
    }


def synthetic_dynamic() -> Dict[str, Any]:
    return {
        "last14DaysCountries": ["CHINA"],
        "last14DaysConfirmed": True,
        "healthDeclarationConfirmed": True,
        "yellowFeverVaccination": "NO",
        "symptoms": "NO",
        "termsAcceptedByUser": False,
        "receiverEmail": SYNTHETIC_EMAIL,
    }


def synthetic_config(today: Optional[dt.date] = None) -> Dict[str, Any]:
    return {
        "profile": synthetic_profile(),
        "trip": synthetic_trip(today),
        "dynamic": synthetic_dynamic(),
        "smoke": True,
        "synthetic": True,
    }


def synthetic_preview_text(trip: Dict[str, Any]) -> str:
    """构造一段与官网预览页同构的可见文本，用于离线验证字段映射。"""
    lines = [
        "Please verify the accuracy of all information before submitting",
        "Date of Arrival :", str(trip.get("arrivalDate", "")),
        "Mode of Travel :", str(trip.get("arrivalMode", "")),
        "Mode of Transport :", str(trip.get("arrivalFlightType", "")),
        "Flight No./Vehicle No. :", str(trip.get("arrivalFlightNo", "")),
        "Date of Departure :", str(trip.get("departureDate", "")),
        "Mode of Travel :", str(trip.get("departureMode", "")),
        "Mode of Transport :", str(trip.get("departureFlightType", "")),
        "Flight No./Vehicle No. :", str(trip.get("departureFlightNo", "")),
        "Country/Territory where you Boarded :", str(trip.get("countryOfBoarding", "")),
        "Purpose of Travel :", str(trip.get("purposeOfTravel", "")),
        "Type of Accommodation in Thailand :", str(trip.get("accommodationType", "")),
        "Province :", str(trip.get("accommodationProvince", "")),
        "District, Area :", str(trip.get("accommodationDistrict", "")),
        "Sub-District, Sub-Area :", str(trip.get("accommodationSubdistrict", "")),
        "Post Code :", str(trip.get("accommodationPostalCode", "")),
        "Address :", str(trip.get("accommodationAddress", "")),
    ]
    return "\n".join(lines)
