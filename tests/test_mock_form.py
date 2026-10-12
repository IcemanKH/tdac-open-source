# -*- coding: utf-8 -*-
"""最小本地 HTML 模拟页的回归测试（G1 最终修复 / P2）。

目的：在没有网络、不访问泰国官网的前提下，验证“字段绑定契约”没有漂移——

  * ``tdac_fill.BIND_JS`` 依赖的 ``formcontrolname`` 必须都能在模拟页里找到；
  * 锚点相对定位（``after(anchor, n)``）依赖的**字段顺序**必须仍然成立；
  * 健康声明与预览页收件邮箱的控件名必须存在；
  * 模拟页必须完全离线、且不含任何真实个人资料。

模拟页是测试夹具，不参与发行（见 ``tools/prepare_release.py`` 的白名单）。
"""
import os
import re
import unittest
from html.parser import HTMLParser

import tdac_fill

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "tdac_mock_form.html")

# BIND_JS 里直接按 formcontrolname 绑定的控件（改 BIND_JS 必须同步改这里，
# 下面的 test_mock_page_covers_bind_js_contract 会双向核对）。
BIND_BY_FC = {
    "familyName", "firstName", "middleName", "passportNo", "occupation", "visaNo",
    "phoneCode", "phoneNo", "arrDate", "flightNo", "deptDate", "deptFlightNo",
    "accPostCode", "accAddress",
}
BIND_SELECTS = {"traPurposeId", "tranModeId", "deptTranModeId", "accTypeId"}
BIND_RADIOS = {"gender", "traModeId", "deptTraModeId"}
HEALTH_RADIOS = {"yellowFever", "symptoms"}


class _Dom(HTMLParser):
    """极简 DOM 收集器：只保留文档顺序、标签名与属性。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []          # [(tag, attrs)]
        self.fields = []            # [{"fc":..., "auto": bool}] 仅 mat-form-field
        self._stack = []            # mat-form-field 嵌套栈
        self.hidden_key_inputs = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        self.elements.append((tag, a))
        if tag == "mat-form-field":
            self._stack.append({"fc": "", "auto": False})
            self.fields.append(self._stack[-1])
        if tag == "input":
            if self._stack:
                cur = self._stack[-1]
                if not cur["fc"] and a.get("formcontrolname"):
                    cur["fc"] = a["formcontrolname"]
                if "mat-mdc-autocomplete-trigger" in a.get("class", ""):
                    cur["auto"] = True
            if a.get("type") == "hidden" and a.get("formcontrolname") == "key":
                self.hidden_key_inputs += 1
        elif a.get("formcontrolname") and self._stack:
            cur = self._stack[-1]
            if not cur["fc"]:
                cur["fc"] = a["formcontrolname"]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag.lower() == "mat-form-field" and self._stack:
            self._stack.pop()

    # --- 模拟 BIND_JS 的锚点相对定位 -------------------------------------
    def after(self, anchor_fc: str, n: int):
        """复刻 BIND_JS 的 ``after()``：从锚点字段起向后取 n 个自动完成框。"""
        idx = next((i for i, f in enumerate(self.fields) if f["fc"] == anchor_fc), None)
        if idx is None:
            return []
        res = []
        for f in self.fields[idx + 1: idx + 1 + 10]:
            if f["auto"]:
                res.append(f)
                if len(res) == n:
                    break
        return res

    def formcontrolnames(self):
        return {a["formcontrolname"] for _t, a in self.elements if a.get("formcontrolname")}


def _load():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        text = fh.read()
    dom = _Dom()
    dom.feed(text)
    return text, dom


class MockFormOfflineSafetyTests(unittest.TestCase):

    def test_fixture_exists(self):
        self.assertTrue(os.path.exists(FIXTURE), FIXTURE)

    def test_mock_page_is_fully_offline(self):
        """模拟页必须完全离线：无脚本、无外链、无网络请求。"""
        text, _dom = _load()
        for forbidden in ("<script", "http://", "https://", "src=", "fetch(", "XMLHttpRequest"):
            self.assertNotIn(forbidden, text.lower(), f"模拟页不应包含 {forbidden!r}")

    def test_mock_page_contains_no_real_personal_data(self):
        """模拟页不得含任何真实资料形态（11 位手机号 / 护照号）。"""
        text, _dom = _load()
        self.assertEqual(re.findall(r"\b1[3-9]\d{9}\b", text), [])
        self.assertEqual(re.findall(r"\b[Ee]\d{8}\b", text), [])
        # 邮箱只允许出现在“占位说明”里，不能有真实收件地址
        self.assertEqual(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.(?:com|cn|net|org)",
                                   text), [])

    def test_mock_page_never_submits(self):
        """模拟页不含可用提交按钮（只有 disabled 占位按钮）。"""
        text, _dom = _load()
        self.assertNotIn("type=\"submit\"", text.lower())
        self.assertIn("disabled", text.lower())


class MockFormBindingContractTests(unittest.TestCase):
    """模拟页必须覆盖 BIND_JS 用到的全部字段钩子与字段顺序。"""

    def setUp(self):
        self.text, self.dom = _load()
        self.fcs = self.dom.formcontrolnames()

    def test_mock_page_covers_bind_js_contract(self):
        """双向核对：BIND_JS 里出现的每个 formcontrolname 都要在模拟页里找到。"""
        js = tdac_fill.BIND_JS
        referenced = set(re.findall(r"formcontrolname=\"([A-Za-z0-9_]+)\"", js)) \
            | set(re.findall(r"(\w+):'\w+'", js))
        expected = BIND_BY_FC | BIND_SELECTS | BIND_RADIOS
        self.assertTrue(expected.issubset(referenced),
                        f"测试期望的字段未在 BIND_JS 中出现：{sorted(expected - referenced)}")
        missing = expected - self.fcs
        self.assertEqual(missing, set(), f"模拟页缺少 BIND_JS 需要的字段：{sorted(missing)}")

    def test_health_and_preview_controls_exist(self):
        for fc in HEALTH_RADIOS:
            self.assertIn(fc, self.fcs, f"缺少健康声明控件 {fc}")
        # 预览页收件邮箱：BIND 之外由 _PREVIEW_EMAIL_SELECTORS 识别
        self.assertIn("email", self.fcs)
        self.assertTrue(self.dom.hidden_key_inputs >= 1,
                        "自动完成框缺少隐藏的 formcontrolname=key 输入")

    def test_anchor_relative_positions_still_hold(self):
        """锚点相对定位依赖的字段顺序必须成立，否则 BIND_JS 会绑错控件。"""
        # 国籍：passportNo 之后第 1 个自动完成框
        self.assertEqual(len(self.dom.after("passportNo", 1)), 1)
        # 出生年/月/日：passportNo 之后第 2~4 个自动完成框
        self.assertEqual(len(self.dom.after("passportNo", 4)), 4)
        self.assertEqual(len(self.dom.after("passportNo", 4)[1:]), 3)
        # 居住国家 / 居住城市：visaNo 之后 2 个
        self.assertEqual(len(self.dom.after("visaNo", 2)), 2)
        # 出发/登机国家地区：arrDate 之后 1 个
        self.assertEqual(len(self.dom.after("arrDate", 1)), 1)
        # 府 / 区 / 分区：accTypeId 之后 3 个
        self.assertEqual(len(self.dom.after("accTypeId", 3)), 3)

    def test_anchors_are_unique(self):
        """锚点字段必须唯一，否则 closest() 取到哪一个就取决于 DOM 顺序。"""
        fcs = [f["fc"] for f in self.dom.fields if f["fc"]]
        anchors = {"passportNo", "visaNo", "arrDate", "accTypeId"}
        for anchor in anchors:
            self.assertEqual(fcs.count(anchor), 1, f"锚点 {anchor} 在模拟页中出现多次")

    def test_binder_requires_exactly_one_critical_control(self):
        """关键控件零匹配或多匹配时必须不绑定，不能让 querySelector 猜第一个。"""
        js = tdac_fill.BIND_JS
        self.assertIn("querySelectorAll", js)
        self.assertIn("count !== 1", js)
        self.assertIn("removeAttribute('data-tdac')", js,
                      "重新绑定前必须清理旧角色，避免动态重复控件留下陈旧标记")


if __name__ == "__main__":
    unittest.main()
